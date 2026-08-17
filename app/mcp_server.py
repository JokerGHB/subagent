"""MCP Server：把整个多 Agent 调研系统暴露为标准 MCP 工具。

运行（stdio 传输，供 Claude Desktop / Cursor 等本地客户端接入）:
    uv run python app/mcp_server.py

学习点：
- stdio 传输下 stdout 是【协议通道】，节点日志必须走 stderr（logging_config.py）。
- research_start 用 asyncio 后台任务跑图：工具立即返回 job_id，客户端用
  research_get_status 轮询、research_get_result 取结果。调研一次要几十秒，
  同步阻塞会卡死模型等待——异步 + 轮询是 agent 调度的常见姿势。
- 工具参数用扁平关键字 + Annotated[..., Field(...)]：MCP 客户端/模型按顶层
  参数调用（{"topic": ...}）。若把单个 Pydantic 模型当参数，FastMCP 会把它
  嵌套到 "params" 键下，不符合 MCP 调用约定。
- 工具命名统一带 research_ 前缀，避免和其它 MCP server 冲突（MCP 最佳实践）。
"""
import asyncio
import json
import logging
import uuid
from enum import Enum
from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from app.graph.builder import build_initial_state, graph
from app.logging_config import setup_logging
from app.search.tavily import search

logger = logging.getLogger("research.mcp")

mcp = FastMCP("research_mcp")

# 内存任务注册表：job_id -> 任务状态。单进程内存即可，重启即清空。
RESEARCH_JOBS: dict[str, dict] = {}


class ResponseFormat(str, Enum):
    """输出格式：markdown 给人看，json 给程序。"""
    MARKDOWN = "markdown"
    JSON = "json"


# ---------- 后台调研任务 ----------

async def _run_research(job_id: str, topic: str) -> None:
    """在事件循环后台跑完整调研流程，完成后写回注册表。"""
    job = RESEARCH_JOBS[job_id]
    try:
        # graph.invoke 是阻塞调用，丢给线程池跑，不卡事件循环
        result = await asyncio.to_thread(graph.invoke, build_initial_state(topic))
        job["status"] = "done"
        job["result"] = result
        job["summary"] = {
            "subtasks": len(result["subtasks"]),
            "sources": len(result["sources"]),
            "facts": len(result["facts"]),
            "key_points": len(result["key_points"]),
        }
    except Exception as e:  # noqa: BLE001 - 后台任务兜底，把错误状态写给客户端
        logger.error("调研失败 job=%s: %s", job_id, type(e).__name__)
        job["status"] = "error"
        job["error"] = str(e)


# ---------- 渲染 ----------

def _result_to_dict(result: dict) -> dict:
    """把图的最终状态序列化成纯 dict（Pydantic 模型转掉）。"""
    return {
        "topic": result["topic"],
        "status": result["status"],
        "subtasks": result["subtasks"],
        "sources": result["sources"],
        "facts": [f.model_dump() for f in result["facts"]],
        "key_points": [kp.model_dump() for kp in result["key_points"]],
    }


def _render_keypoints_markdown(result: dict) -> str:
    """把关键数据点渲染成人可读的 Markdown 报告。"""
    lines = [f"# 调研报告：{result['topic']}", ""]
    if not result["key_points"]:
        lines.append("（无关键数据点）")
        return "\n".join(lines)
    for kp in result["key_points"]:
        flag = f" ⚠️ {kp.conflict}" if kp.conflict else ""
        lines.append(f"## {kp.dimension}: {kp.value}{kp.unit} ({kp.time}){flag}")
        lines.append(f"- 印证来源数：{kp.source_count}")
        if kp.quote:
            lines.append(f"- 原文摘录：{kp.quote}")
        for u in kp.sources[:3]:
            lines.append(f"- 来源：{u}")
        lines.append("")
    return "\n".join(lines)


# ---------- 工具 ----------

@mcp.tool(
    name="research_start",
    annotations={
        "title": "发起一次调研",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def research_start(
    topic: Annotated[
        str,
        Field(
            min_length=2,
            max_length=200,
            description="调研主题，如 '国产大模型市场分析'",
        ),
    ],
) -> str:
    """发起一次多 Agent 调研（规划→并行搜索→去重→抽取→分析），后台异步执行。

    Args:
        topic (str): 调研主题

    Returns:
        str: JSON 字符串，含 job_id 与初始状态。随后用 research_get_status
            轮询进度，research_get_result 取最终结果。

    示例：
        - 输入 topic="国产大模型市场分析" → {"job_id": "...", "status": "running"}
        - 不要用于：只想快速搜几条结果（用 research_search_web）
    """
    job_id = uuid.uuid4().hex[:12]
    RESEARCH_JOBS[job_id] = {"id": job_id, "topic": topic, "status": "running"}
    asyncio.create_task(_run_research(job_id, topic))
    logger.info("发起调研 job=%s topic=%s", job_id, topic)
    return json.dumps(
        {"job_id": job_id, "status": "running", "topic": topic},
        ensure_ascii=False,
    )


@mcp.tool(
    name="research_get_status",
    annotations={
        "title": "查询调研任务进度",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def research_get_status(
    job_id: Annotated[
        str,
        Field(min_length=1, description="research_start 返回的任务 ID"),
    ],
) -> str:
    """查询一次调研任务的当前状态（running / done / error / not_found）。

    Args:
        job_id (str): research_start 返回的任务 ID

    Returns:
        str: JSON 字符串，含 status、各阶段产出计数（done 后才有）。
    """
    job = RESEARCH_JOBS.get(job_id)
    if job is None:
        return json.dumps({"job_id": job_id, "status": "not_found"}, ensure_ascii=False)
    resp = {"job_id": job["id"], "status": job["status"], "topic": job["topic"]}
    resp.update(job.get("summary", {}))
    if job.get("error"):
        resp["error"] = job["error"]
    return json.dumps(resp, ensure_ascii=False)


@mcp.tool(
    name="research_get_result",
    annotations={
        "title": "获取调研结果",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def research_get_result(
    job_id: Annotated[
        str,
        Field(min_length=1, description="research_start 返回的任务 ID"),
    ],
    response_format: ResponseFormat = ResponseFormat.MARKDOWN,
) -> str:
    """获取一次调研的最终结果（关键数据点 + 事实 + 来源）。

    Args:
        job_id (str): research_start 返回的任务 ID
        response_format (str): 'markdown'（人读）或 'json'（程序读）

    Returns:
        str: markdown 报告，或完整 JSON 数据；任务未完成时返回当前状态。
    """
    job = RESEARCH_JOBS.get(job_id)
    if job is None:
        return json.dumps({"error": "任务不存在，job_id 是否正确？"}, ensure_ascii=False)
    if job["status"] != "done":
        return json.dumps(
            {"job_id": job["id"], "status": job["status"], "tip": "请稍后重试"},
            ensure_ascii=False,
        )
    result = job["result"]
    if response_format == ResponseFormat.JSON:
        return json.dumps(_result_to_dict(result), ensure_ascii=False, indent=2)
    return _render_keypoints_markdown(result)


@mcp.tool(
    name="research_search_web",
    annotations={
        "title": "直接搜索网页",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def research_search_web(
    query: Annotated[
        str,
        Field(min_length=2, max_length=200, description="搜索关键词，如 '中国AI市场规模 2025'"),
    ],
    max_results: Annotated[int, Field(ge=1, le=10, description="返回结果条数")] = 5,
) -> str:
    """直接调用 Tavily 搜索网页（不走完整调研流程），返回标题/链接/摘要。

    Args:
        query (str): 搜索关键词
        max_results (int): 返回条数，1~10，默认 5

    Returns:
        str: Markdown 格式的搜索结果列表。
    """
    try:
        results = await asyncio.to_thread(search, query, max_results)
    except Exception as e:  # noqa: BLE001 - 搜索失败给出可读提示
        return f"搜索失败：{type(e).__name__}: {e}"
    if not results:
        return f"没有搜到与 '{query}' 相关的结果"
    lines = [f"# 搜索结果：{query}", ""]
    for i, r in enumerate(results, 1):
        lines.append(f"## {i}. {r.get('title', '(无标题)')}")
        lines.append(f"URL: {r.get('url', '')}")
        content = r.get("content", "").strip()
        if content:
            lines.append(content[:300])
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    setup_logging()
    mcp.run()  # 默认 stdio 传输

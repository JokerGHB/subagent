"""MCP server 冒烟测试（进程内 Client，不发真实调研任务）。

用 fastmcp.Client 直接连 FastMCP 实例，验证：工具注册、网页搜索、错误处理、
结果渲染、进度透出。完整调研链路（research_start 在后台跑全图）成本高，单独手动验证。
"""
import asyncio
import json
import time

from fastmcp import Client

from app import mcp_server
from app.mcp_server import _render_keypoints_markdown, mcp
from app.models.schemas import KeyPoint


def _run(coro):
    return asyncio.run(coro)


def test_tools_registered():
    """四个工具都应注册成功。"""
    async def check():
        async with Client(mcp) as client:
            tools = await client.list_tools()
            names = {t.name for t in tools}
            assert {
                "research_start",
                "research_get_status",
                "research_get_result",
                "research_search_web",
            } <= names

    _run(check())


def test_search_web_returns_markdown():
    """直接搜索返回 Markdown 结果（走真实 Tavily，快速）。"""
    async def check():
        async with Client(mcp) as client:
            result = await client.call_tool(
                "research_search_web",
                {"query": "中国AI市场规模 2025", "max_results": 3},
            )
            text = result.content[0].text
            assert "搜索结果" in text
            assert "URL:" in text

    _run(check())


def test_job_not_found():
    """不存在的 job_id 应返回明确的错误/未找到。"""
    async def check():
        async with Client(mcp) as client:
            r = await client.call_tool(
                "research_get_status", {"job_id": "deadbeef0000"}
            )
            assert json.loads(r.content[0].text)["status"] == "not_found"

            r2 = await client.call_tool(
                "research_get_result", {"job_id": "deadbeef0000"}
            )
            assert "不存在" in r2.content[0].text

    _run(check())


def test_render_keypoints_markdown():
    """关键数据点渲染成 Markdown 报告。"""
    kp = KeyPoint(
        dimension="市场规模",
        value=294.16,
        unit="亿元",
        time="2024年",
        source_count=2,
        sources=["https://example.com/a"],
        quote="市场规模达294亿元",
        conflict="",
    )
    md = _render_keypoints_markdown({"topic": "测试主题", "key_points": [kp]})
    assert "# 调研报告：测试主题" in md
    assert "市场规模: 294.16亿元 (2024年)" in md
    assert "印证来源数：2" in md


# ---------- 进度透出（与 HTTP 层同构） ----------

_PROGRESS_SNAPSHOT = {
    "stage": "writing",
    "label": "正在撰写报告…",
    "percent": 80,
    "done": 0,
    "total": 0,
    "elapsed": 60.0,
    "eta": 20.0,
}


def _fake_job(monkeypatch, job_id: str = "progress0001") -> dict[str, dict]:
    """把 MCP 的 job 注册表换成测试私有 dict（避免污染其它测试）。"""
    jobs = {
        job_id: {
            "id": job_id,
            "topic": "进度主题",
            "status": "running",
            "created_at": time.time(),
        }
    }
    monkeypatch.setattr(mcp_server, "RESEARCH_JOBS", jobs)
    return jobs


def _good_result(topic: str = "进度主题") -> dict:
    return {
        "topic": topic,
        "status": "written",
        "subtasks": [],
        "sources": [],
        "facts": [],
        "key_points": [],
        "report": "# 报告",
    }


def test_run_research_reports_progress_to_status(monkeypatch):
    """后台任务把进度写进 job，research_get_status 轮询就能看到阶段与 ETA。"""
    job_id = "progress0001"
    jobs = _fake_job(monkeypatch, job_id)
    captured = {}

    def fake_invoke(topic, force=False, user_id=None, research_id=None, on_progress=None):
        captured["cb"] = on_progress
        on_progress(_PROGRESS_SNAPSHOT)
        return _good_result(topic)

    monkeypatch.setattr(mcp_server, "invoke_research", fake_invoke)
    _run(mcp_server._run_research(job_id, jobs[job_id]["topic"]))

    # 跑完后是终态（100% / done），覆盖掉过程快照
    assert jobs[job_id]["status"] == "done"
    assert jobs[job_id]["progress"]["stage"] == "done"
    assert jobs[job_id]["progress"]["percent"] == 100

    # 手动再喂一条过程快照 → 证明回调写的就是这个 job，且工具会把它透出来
    captured["cb"](_PROGRESS_SNAPSHOT)

    async def check():
        async with Client(mcp) as client:
            r = await client.call_tool("research_get_status", {"job_id": job_id})
            return json.loads(r.content[0].text)

    resp = _run(check())
    assert resp["progress"]["percent"] == 80
    assert resp["progress"]["label"] == "正在撰写报告…"
    assert resp["progress"]["eta"] == 20.0


def test_run_research_failure_progress_is_failed_stage(monkeypatch):
    """报告为空 → status=error + 进度标 failed（前端/客户端标红，不停在半路）。"""
    job_id = "progress0002"
    jobs = _fake_job(monkeypatch, job_id)

    def fake_invoke(topic, force=False, user_id=None, research_id=None, on_progress=None):
        result = _good_result(topic)
        result["report"] = ""
        result["error"] = "分析失败：PermissionDeniedError"
        return result

    monkeypatch.setattr(mcp_server, "invoke_research", fake_invoke)
    _run(mcp_server._run_research(job_id, jobs[job_id]["topic"]))

    job = jobs[job_id]
    assert job["status"] == "error"
    assert "分析失败" in job["error"]
    assert job["progress"]["stage"] == "failed"
    assert job["progress"]["eta"] is None

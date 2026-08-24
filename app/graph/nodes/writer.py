"""报告撰写 Agent：把关键数据点组装成正式 Markdown 报告。

学习点：
- writer 是流水线最后一级，也是最后一个单写点，所以写 status 安全
  （LastValue 通道每 superstep 只允许一次写，writer 只有它在写）。
- 直接让模型输出 Markdown 正文，不用 with_structured_output(Report)：
  报告最终是给人读的，不需要被程序解析；省掉 JSON 包裹和 schema 推理，
  生成能快约一倍。结构化约束留给 extractor/analyzer（它们的数据要被
  下游程序消费，必须有 schema）。
- 字数统计用去掉 URL/语法符号后的有效正文，避免 URL 虚高字数。
"""
import logging
import re
from typing import cast

from app.graph.prompts import WRITER_PROMPT
from app.graph.state import ResearchState
from app.models.llm import get_writer_llm

logger = logging.getLogger("research.nodes.writer")


def _format_key_points(state: ResearchState) -> str:
    """把 KeyPoint 渲染成喂给模型的紧凑文本（和评测渲染类似，带冲突标记）。"""
    lines = []
    for i, kp in enumerate(state["key_points"], 1):
        flag = f" ⚠️冲突: {kp.conflict}" if kp.conflict else ""
        lines.append(
            f"{i}. {kp.dimension}: {kp.value}{kp.unit} "
            f"({kp.time}) 印证{len(kp.sources)}源{flag}"
        )
        if kp.quote:
            lines.append(f"   原文: {kp.quote[:80]}")
        for u in kp.sources[:3]:
            lines.append(f"   来源: {u}")
    return "\n".join(lines) if lines else "(无关键数据点)"


def writer_node(state: ResearchState) -> dict:
    if not state["key_points"]:
        logger.warning("无关键数据点，跳过报告生成")
        return {"report": "", "status": "written"}

    llm = get_writer_llm()
    prompt = WRITER_PROMPT.format(
        topic=state["topic"],
        key_points=_format_key_points(state),
    )
    try:
        # invoke 返回 AIMessage，.content 就是 Markdown 正文字符串
        # cast：content 类型标注可能是 str | list，运行时是纯 str
        md = cast(str, llm.invoke(prompt).content)
    except Exception as e:  # noqa: BLE001 - 报告失败也不拖垮流程
        logger.warning("报告生成失败: %s", type(e).__name__)
        return {"report": "", "status": "written"}

    length = len(_strip_formatting(md))
    logger.info("报告生成完成（正文 %s 字）", length)
    # 兜底提示：prompt 要求 800~1000 字，明显超标就提醒（不强行截断，避免破坏 Markdown 结构）
    if length > 1300:
        logger.warning("报告正文 %s 字，超出 800~1000 字目标较多，可检查 WRITER_PROMPT 字数约束", length)
    return {"report": md, "status": "written"}


def _strip_formatting(md: str) -> str:
    """统计「有效正文」字数：去掉 URL、Markdown 语法符号和空白。

    之前直接 len(md) 会把 URL（[url](url) 里每个 URL 出现两次）和 # ** 等
    语法符号全算进去，导致 800~1000 字的目标被虚高成 2000+ 字误报警。
    """
    return re.sub(r"https?://\S+|[#*_`\[\]()\-]|\s", "", md)

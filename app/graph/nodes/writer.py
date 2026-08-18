"""报告撰写 Agent：把关键数据点组装成正式 Markdown 报告。

学习点：
- writer 是流水线最后一级，也是最后一个单写点，所以写 status 安全
  （LastValue 通道每 superstep 只允许一次写，writer 只有它在写）。
- 先让模型按 schema 产出结构化 Report，再用纯函数 render_report_markdown
  渲染成 Markdown —— 渲染逻辑不进模型，可离线单测、输出稳定。
- 用最强的 qwen-max：整轮只调用一次，贵得有道理（输出质量决定 Demo 效果）。
"""
import logging

from app.graph.prompts import WRITER_PROMPT
from app.graph.state import ResearchState
from app.models.llm import get_writer_llm
from app.models.schemas import Report

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


def render_report_markdown(report: Report) -> str:
    """把结构化 Report 渲染成最终 Markdown。纯函数，可单测。"""
    md = [f"# {report.title}", "", report.overview, ""]
    if report.sections:
        md += ["## 关键发现", ""]
        for sec in report.sections:
            md += [f"### {sec.title}", "", sec.body]
            if sec.sources:
                md += ["", "**来源：** " + " · ".join(f"[{u}]({u})" for u in sec.sources)]
            md.append("")
    md += ["## 结论", "", report.conclusion]
    # 汇总全部来源 URL（保持出现顺序去重，代码做不靠模型）
    seen: list[str] = []
    for sec in report.sections:
        for u in sec.sources:
            if u not in seen:
                seen.append(u)
    if seen:
        md += ["", "## 来源附录", ""]
        md += [f"- [{u}]({u})" for u in seen]
    return "\n".join(md).strip() + "\n"


def writer_node(state: ResearchState) -> dict:
    if not state["key_points"]:
        logger.warning("无关键数据点，跳过报告生成")
        return {"report": "", "status": "written"}

    llm = get_writer_llm().with_structured_output(Report)
    prompt = WRITER_PROMPT.format(
        topic=state["topic"],
        key_points=_format_key_points(state),
    )
    try:
        report: Report = llm.invoke(prompt)
    except Exception as e:  # noqa: BLE001 - 报告失败也不拖垮流程
        logger.warning("报告生成失败: %s", type(e).__name__)
        return {"report": "", "status": "written"}

    md = render_report_markdown(report)
    logger.info("报告生成完成（%s 字，%s 个小节）", len(md), len(report.sections))
    return {"report": md, "status": "written"}

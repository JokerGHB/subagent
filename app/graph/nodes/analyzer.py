"""数据分析 Agent：对全部抽取事实做交叉验证、去噪、找共识，产出关键数据点。

学习点：
- analyzer 在 extractor 扇出【之后】串行跑一次——因为要看完全部事实才能分析，
  这就是「Map → Reduce → Map」流水线里的第二个 Reduce。
- 先由代码把 162 条事实压缩成按维度分组、每组取 top3 的 digest，
  再喂给模型，避免海量输入稀释注意力、也省 token。
- 模型可能编造来源 URL，代码在最后做「溯源兜底」：只保留真实出现过的 URL。
"""
import logging

from app.analysis.grouping import group_facts_by_dimension, select_top_facts
from app.graph.prompts import ANALYZER_PROMPT
from app.graph.state import ResearchState
from app.models.llm import get_analyzer_llm
from app.models.schemas import AnalysisResult

logger = logging.getLogger("research.nodes.analyzer")


def _build_digest(state: ResearchState) -> str:
    """把全部事实压成紧凑文本：按维度分组、每组取置信度最高的前 3 条。"""
    cred_map = {s["url"]: s["credibility"] for s in state["sources"]}
    groups = group_facts_by_dimension(state["facts"])
    lines = []
    for dim in sorted(groups):
        for f in select_top_facts(groups[dim], k=3):
            cred = cred_map.get(f.source_url, 0.5)
            lines.append(
                f"- [{dim}] {f.value}{f.unit} (时间:{f.time} "
                f"置信:{f.confidence:.1f} 可信:{cred:.1f}) "
                f"来源:{f.source_url} 原文:{f.quote}"
            )
    return "\n".join(lines) if lines else "(无事实)"


def analyzer_node(state: ResearchState) -> dict:
    if not state["facts"]:
        logger.warning("无事实可分析")
        return {"key_points": [], "status": "analyzed"}

    llm = get_analyzer_llm().with_structured_output(AnalysisResult)
    prompt = ANALYZER_PROMPT.format(facts=_build_digest(state))
    try:
        result: AnalysisResult = llm.invoke(prompt)
    except Exception as e:  # noqa: BLE001 - 边界容错：分析失败也不拖垮流程
        logger.warning("分析失败: %s", type(e).__name__)
        return {"key_points": [], "status": "analyzed"}

    # 溯源兜底：模型可能编造 URL，只保留真实出现在事实列表里的来源
    valid_urls = {f.source_url for f in state["facts"]}
    for kp in result.key_points:
        kp.sources = [u for u in kp.sources if u in valid_urls][:3]
        # 模型常把"无冲突"写成"无/无矛盾"，归一化成空串，只在真冲突时显示 ⚠️
        if kp.conflict.strip().lower() in {"", "无", "无冲突", "无矛盾", "无分歧", "none"}:
            kp.conflict = ""

    logger.info("产出 %s 个关键数据点", len(result.key_points))
    return {"key_points": result.key_points, "status": "analyzed"}

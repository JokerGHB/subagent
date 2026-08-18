"""评测 Agent：用最强模型（qwen-max）对调研产出按 4 指标打分。

学习点：
- LLM-as-a-Judge 是评测 agent 系统的常见做法：不写死规则，让最强模型
  按固定维度打分，比正则/规则判断更能抓到"质量"这种主观维度。
- 打分从严不从宽：评测的价值在于区分度，都打满分等于没测。
- 四个指标对应系统的核心卖点：可溯源（防幻觉）、冲突标注、覆盖度。
"""
import logging
from typing import cast

from app.graph.prompts import JUDGE_PROMPT
from app.models.llm import get_judge_llm
from app.models.schemas import JudgeResult, MetricScore

logger = logging.getLogger("research.eval.judge")


def format_key_points(result: dict) -> str:
    """把关键数据点渲染成评测用的紧凑文本（输入是纯 dict）。"""
    lines = []
    for i, kp in enumerate(result.get("key_points", []), 1):
        flag = f" ⚠️冲突: {kp['conflict']}" if kp.get("conflict") else ""
        lines.append(
            f"{i}. {kp['dimension']}: {kp['value']}{kp['unit']} "
            f"({kp['time']}) 印证{len(kp.get('sources', []))}源{flag}"
        )
        if kp.get("quote"):
            lines.append(f"    原文: {kp['quote'][:80]}")
        for u in kp.get("sources", [])[:2]:
            lines.append(f"    来源: {u}")
    return "\n".join(lines) if lines else "(无关键数据点)"


def judge_result(result: dict, topic: str) -> JudgeResult:
    """对一份调研结果（纯 dict 形式）打分，返回 JudgeResult。"""
    llm = get_judge_llm().with_structured_output(JudgeResult)
    prompt = JUDGE_PROMPT.format(
        topic=topic,
        key_points=format_key_points(result),
    )
    try:
        # cast：with_structured_output 的返回类型标注是 _DictOrPydantic，
        # 运行时实际是 JudgeResult（静态检查器看不到这一点）
        jr = cast(JudgeResult, llm.invoke(prompt))
    except Exception as e:  # noqa: BLE001 - 评测失败不拖垮流程
        logger.warning("评测失败: %s", type(e).__name__)
        # 构造一个全 0 的占位结果，调用方打印时能看出评测失败
        reason = f"评测失败: {type(e).__name__}"
        return JudgeResult(
            relevance=MetricScore(score=0, reason=reason),
            groundedness=MetricScore(score=0, reason=""),
            completeness=MetricScore(score=0, reason=""),
            conflict_handling=MetricScore(score=0, reason=""),
            overall="评测失败，无法打分",
        )
    return jr

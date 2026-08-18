"""评测脚本：对调研产出用 qwen-max 打 4 项指标分（相关性/可溯源/覆盖度/冲突处理）。

两种用法：
  1. 复用上次结果（不传主题）:  uv run python -m scripts.eval_research
  2. 现场新调研再评测:          uv run python -m scripts.eval_research "主题"

学习点：
- 用「最近的落盘结果」评测，避免每次评测都重跑 3 分钟调研。
- LLM-as-a-Judge：评测本身也交给模型，指标固定、从严打分。
"""
import sys

from app.eval.judge import format_key_points, judge_result
from app.logging_config import setup_logging
from app.service import invoke_research, load_last_result, serialize_result

# 指标名 → 中文名
_METRIC_CN = {
    "relevance": "相关性",
    "groundedness": "可溯源性",
    "completeness": "覆盖度",
    "conflict_handling": "冲突处理",
}


def main() -> None:
    setup_logging()
    topic_arg = sys.argv[1] if len(sys.argv) > 1 else ""

    if topic_arg:
        print(f"现场调研：{topic_arg}（约 3 分钟，请稍候）...")
        result = serialize_result(invoke_research(topic_arg))
        topic = topic_arg
    else:
        result = load_last_result()
        if result is None:
            print(
                "未找到 data/last_result.json，且没传主题。\n"
                "请先跑 uv run python run.py \"主题\"，或直接传主题参数现场调研。"
            )
            return
        topic = result["topic"]
        print(f"复用上次结果：{topic}（{len(result['key_points'])} 个关键数据点）")

    print("\n===== 待评测关键数据点 =====")
    print(format_key_points(result))

    print("\n===== 评测打分（qwen-max）=====")
    jr = judge_result(result, topic)
    total = 0
    for field, cn in _METRIC_CN.items():
        ms = getattr(jr, field)
        total += ms.score
        print(f"  {cn}: {ms.score}/5  ——  {ms.reason}")
    print(f"\n  平均分: {total / len(_METRIC_CN):.1f}/5")
    print(f"  综合评价: {jr.overall}")


if __name__ == "__main__":
    main()

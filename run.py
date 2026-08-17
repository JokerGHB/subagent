"""P3 入口：规划 → 搜索 → 去重 → 抽取 → 数据分析。

运行: uv run python run.py "调研主题"（不传主题则用默认示例）
"""
import sys

from app.graph.builder import build_initial_state, graph
from app.logging_config import setup_logging


def main() -> None:
    setup_logging()
    topic = sys.argv[1] if len(sys.argv) > 1 else "国产大模型市场分析"

    result = graph.invoke(build_initial_state(topic))

    print("\n=== 最终状态 ===")
    print(f"topic   : {result['topic']}")
    print(f"子任务数 : {len(result['subtasks'])}")
    print(f"来源数   : {len(result['sources'])}")
    print(f"事实数   : {len(result['facts'])}")
    print(f"关键点   : {len(result['key_points'])}")
    print(f"status  : {result['status']}")

    print("\n--- 来源列表（前 5 条）---")
    for s in result["sources"][:5]:
        print(f"  [可信度 {s['credibility']}] {s['title']} | {s['url']}")
    if len(result["sources"]) > 5:
        print(f"  ... 其余 {len(result['sources']) - 5} 条")

    print("\n--- 抽取的事实（前 10 条）---")
    for f in result["facts"][:10]:
        print(f"  [{f.confidence:.1f}] {f.dimension}: {f.value}{f.unit} ({f.time}) | {f.source_url[:50]}")
    if len(result["facts"]) > 10:
        print(f"  ... 其余 {len(result['facts']) - 10} 条")

    print("\n--- 关键数据点 ---")
    for kp in result["key_points"]:
        flag = f"  ⚠️ {kp.conflict}" if kp.conflict else ""
        print(f"  [{kp.confidence:.1f}] {kp.dimension}: {kp.value}{kp.unit} "
              f"({kp.time}) 印证 {kp.source_count} 源{flag}")
        if kp.quote:
            print(f"      原文: {kp.quote[:60]}")
        for u in kp.sources[:2]:
            print(f"      来源: {u}")


if __name__ == "__main__":
    main()

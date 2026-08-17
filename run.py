"""P2 入口：规划 → 并行搜索 → 去重 → 并行抽取。

运行: uv run python run.py "调研主题"（不传主题则用默认示例）
"""
import sys

from app.graph.builder import graph


def main() -> None:
    topic = sys.argv[1] if len(sys.argv) > 1 else "国产大模型市场分析"

    result = graph.invoke(
        {
            "topic": topic,
            "subtasks": [],
            "sources": [],
            "facts": [],
            "status": "",
            "report": "",
        }
    )

    print("\n=== 最终状态 ===")
    print(f"topic   : {result['topic']}")
    print(f"子任务数 : {len(result['subtasks'])}")
    print(f"来源数   : {len(result['sources'])}")
    print(f"事实数   : {len(result['facts'])}")
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


if __name__ == "__main__":
    main()

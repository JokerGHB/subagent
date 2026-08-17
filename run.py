"""P1 入口：真实规划 + 并行搜索。

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
            "status": "",
            "report": "",
        }
    )

    print("\n=== 最终状态 ===")
    print(f"topic   : {result['topic']}")
    print(f"子任务数 : {len(result['subtasks'])}")
    print(f"来源数   : {len(result['sources'])}")
    print(f"status  : {result['status']}")
    print("\n--- 来源列表 ---")
    for s in result["sources"]:
        print(f"  [可信度 {s['credibility']}] {s['title']} | {s['url']}")


if __name__ == "__main__":
    main()

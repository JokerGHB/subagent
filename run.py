"""P0 入口：运行最小图，验证全链路工具链。

运行: uv run python run.py
"""
from app.graph.builder import graph


def main() -> None:
    result = graph.invoke(
        {
            "topic": "国产大模型市场分析",
            "subtasks": [],
            "sources": [],
            "status": "",
            "report": "",
        }
    )
    print("\n=== 最终状态 ===")
    for key, value in result.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()

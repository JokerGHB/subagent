"""统一调研入口：跑图 + 可观测 + 结果落盘。CLI（run.py）和 MCP server 共用。

学习点：
- 把「构造状态 → 跑图 → 记录 trace → 存结果」收敛到一个函数，两个入口复用，
  以后加观测、加缓存都只改这一处。
- Langfuse 是可选依赖：没配 key 就静默跳过（返回 None），不拖垮主流程。
  可观测性永远不能成为业务的前置条件。
"""
import json
import os
from pathlib import Path
from typing import Any

from app.graph.builder import build_initial_state, graph
from config.settings import settings

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
RESULT_FILE = DATA_DIR / "last_result.json"


def _langfuse_callback() -> Any:
    """配置了 Langfuse 才返回回调对象；否则返回 None（优雅降级）。"""
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return None
    # langfuse SDK 从环境变量读 key，把 settings 里的值注入环境变量
    os.environ["LANGFUSE_PUBLIC_KEY"] = settings.langfuse_public_key
    os.environ["LANGFUSE_SECRET_KEY"] = settings.langfuse_secret_key
    os.environ["LANGFUSE_HOST"] = settings.langfuse_host
    from langfuse.langchain import CallbackHandler  # 延迟导入：未装也不崩

    return CallbackHandler()


def serialize_result(result: dict) -> dict:
    """把图的最终状态序列化成纯 dict（Pydantic 模型转掉）。"""
    return {
        "topic": result["topic"],
        "status": result["status"],
        "subtasks": result["subtasks"],
        "sources": result["sources"],
        "facts": [f.model_dump() for f in result["facts"]],
        "key_points": [kp.model_dump() for kp in result["key_points"]],
    }


def save_result(result: dict) -> Path:
    """把结果落盘到 data/last_result.json，供评测脚本复用（不重复跑调研）。"""
    DATA_DIR.mkdir(exist_ok=True)
    RESULT_FILE.write_text(
        json.dumps(serialize_result(result), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return RESULT_FILE


def load_last_result() -> dict | None:
    """读取最近一次落盘的调研结果；不存在则返回 None。"""
    if not RESULT_FILE.exists():
        return None
    return json.loads(RESULT_FILE.read_text(encoding="utf-8"))


def invoke_research(topic: str) -> dict:
    """跑一次完整调研，返回图的最终状态 dict。

    - 带 Langfuse 回调（配置了才带）
    - 结果落盘 data/last_result.json
    """
    config: dict = {}
    handler = _langfuse_callback()
    if handler is not None:
        config = {"callbacks": [handler]}
    result = graph.invoke(build_initial_state(topic), config=config)
    save_result(result)
    return result

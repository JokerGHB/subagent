"""统一调研入口：跑图 + 可观测 + 结果落盘 + SQLite 历史 + Redis 缓存。

CLI（run.py）、MCP server、FastAPI（app/api.py）三端共用这一个入口。
命中缓存的请求秒回、不烧 token；无缓存的请求带「防击穿锁」跑真调研。

学习点：
- 缓存编排的顺序很讲究：先查缓存（省）→ 抢锁（防击穿）→ 真调研 →
  写完缓存再释放锁。锁没抢到的人短轮询等结果，而不是重复烧钱。
- 外部依赖（Langfuse / Redis / SQLite）全部降级可用：坏了就多花点时间跑真调研，
  绝不阻塞主流程。
"""
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from app.graph.builder import build_initial_state, graph
from app.models.schemas import Fact, KeyPoint
from app.storage import cache, db
from config.settings import settings

logger = logging.getLogger("research.service")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
RESULT_FILE = DATA_DIR / "last_result.json"
REPORT_FILE = DATA_DIR / "report.md"

# 没抢到重建锁时，轮询缓存的次数与间隔（共约 10 秒）
_LOCK_WAIT_ROUNDS = 5
_LOCK_WAIT_SECONDS = 2


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
    """把图的最终状态序列化成纯 dict（Pydantic 模型转掉），供 JSON 存储/传输。"""
    return {
        "topic": result["topic"],
        "status": result["status"],
        "subtasks": result["subtasks"],
        "sources": result["sources"],
        "facts": [f.model_dump() for f in result["facts"]],
        "key_points": [kp.model_dump() for kp in result["key_points"]],
        "report": result["report"],
    }


def deserialize_result(data: dict) -> dict:
    """把序列化结果还原成图状态形状（Pydantic 对象）。

    缓存命中返回的是纯 dict，而下游（MCP 渲染、run.py 打印）用属性访问
    （kp.conflict、f.confidence），所以要还原成与图状态一致的结构。
    """
    return {
        "topic": data["topic"],
        "status": data["status"],
        "subtasks": data.get("subtasks", []),
        "sources": data.get("sources", []),
        "facts": [Fact(**f) for f in data.get("facts", [])],
        "key_points": [KeyPoint(**kp) for kp in data.get("key_points", [])],
        "report": data.get("report", ""),
    }


def save_result(result: dict) -> Path:
    """把结果落盘，供评测脚本复用（不重复跑调研）。

    - data/last_result.json：全量结构化结果（评测用）
    - data/report.md：报告单独存成 Markdown 文件，可直接拿去用/演示
    """
    DATA_DIR.mkdir(exist_ok=True)
    RESULT_FILE.write_text(
        json.dumps(serialize_result(result), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    REPORT_FILE.write_text(result.get("report", ""), encoding="utf-8")
    return RESULT_FILE


def load_last_result() -> dict | None:
    """读取最近一次落盘的调研结果；不存在则返回 None。"""
    if not RESULT_FILE.exists():
        return None
    return json.loads(RESULT_FILE.read_text(encoding="utf-8"))


def _empty_result(topic: str) -> dict:
    """空值缓存命中时返回的空结果（status=empty，下游按无数据渲染）。"""
    return {
        "topic": topic,
        "status": "empty",
        "subtasks": [],
        "sources": [],
        "facts": [],
        "key_points": [],
        "report": "",
    }


def _run_and_store(topic: str) -> dict:
    """真调研 + 三处落盘：文件（保留兼容）、SQLite（历史）、Redis（缓存）。"""
    config: dict = {}
    handler = _langfuse_callback()
    if handler is not None:
        config = {"callbacks": [handler]}
    result = graph.invoke(build_initial_state(topic), config=config)

    save_result(result)               # 文件落盘（兼容旧行为）
    db.save_research_record(result)   # SQLite 历史（新增）

    serialized = serialize_result(result)
    if serialized["key_points"]:
        cache.cache_set(topic, serialized)   # 有产出 → 正常缓存（24h）
    else:
        cache.cache_set_empty(topic)         # 无产出 → 空值缓存（5 分钟，防穿透）
    return result


def _wait_or_run(topic: str) -> dict:
    """没抢到重建锁：短轮询缓存，等持锁请求重建完；超时兜底直接跑。"""
    for _ in range(_LOCK_WAIT_ROUNDS):
        time.sleep(_LOCK_WAIT_SECONDS)
        cached = cache.cache_get(topic)
        if cached == {}:
            return _empty_result(topic)
        if cached is not None:
            return deserialize_result(cached)
    # 超时兜底：直接真调研（最坏多跑一次，但不会无限等锁）
    logger.info("等待重建锁超时，兜底直接调研: %s", topic)
    return _run_and_store(topic)


def invoke_research(topic: str, force: bool = False) -> dict:
    """跑一次完整调研，返回图最终状态形状的 dict。

    编排顺序（省 token 优先）：
    1. force=False 且缓存命中 → 直接返回（秒回，不烧 token）
    2. 未命中 → 抢「重建锁」；抢到的去真调研，没抢到的轮询等结果
    3. 真调研后写文件 + SQLite + Redis；无产出写空值缓存（防穿透）
    """
    # 1) 缓存命中（非强制刷新）→ 直接返回
    if not force:
        cached = cache.cache_get(topic)
        if cached == {}:
            # 空值缓存命中：该主题刚调研过、无结果，也短路（省 token）
            logger.info("缓存空值命中，跳过调研: %s", topic)
            return _empty_result(topic)
        if cached is not None:
            logger.info("缓存命中，直接返回: %s", topic)
            return deserialize_result(cached)

    # 2) 抢重建锁（防击穿）：同主题并发只有一个真调研
    if cache.acquire_rebuild_lock(topic):
        try:
            return _run_and_store(topic)
        finally:
            cache.release_rebuild_lock(topic)

    # 3) 没抢到锁：轮询等持锁请求写完缓存
    return _wait_or_run(topic)

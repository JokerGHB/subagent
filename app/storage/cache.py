"""Redis 同主题缓存层：处理缓存三大问题（穿透 / 击穿 / 雪崩）。

学习点：
- 缓存不是"存了取出来"就完事，三个经典坑：
  1. 穿透：反复查"不存在的主题"（或主题无结果）→ 每次都打到 LLM，白白烧钱
  2. 击穿：某个热点主题的缓存刚过期瞬间，大量并发同时来 → 全去真调研
  3. 雪崩：大量主题缓存同时过期 → 一瞬间后端被打爆
  应对：空值缓存（穿透）、互斥锁（击穿）、TTL 抖动（雪崩）。
- Redis 是外部依赖，连不上绝不能阻塞主流程——一律降级为"未命中，直接真调研"。
"""
import json
import logging
import random

import redis

from config.settings import settings

logger = logging.getLogger("research.storage.cache")

# 模块级单例客户端（惰性连接；测试可替换为 fakeredis）
_redis_client: redis.Redis | None = None


def _client() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


def normalize_topic(topic: str) -> str:
    """缓存键归一化：去全部空白 + 小写，让"AI 市场规模"和"ai市场规模"命中同一缓存。"""
    return "".join(topic.split()).lower()


def _cache_key(topic: str) -> str:
    return f"research:cache:{normalize_topic(topic)}"


def _lock_key(topic: str) -> str:
    return f"research:lock:{normalize_topic(topic)}"


def cache_get(topic: str) -> dict | None:
    """取缓存。返回 None 表示未命中或 Redis 不可用；返回 {} 表示命中"空值缓存"。

    空值缓存命中也是"知道了结果"（主题刚调研过、没结果），调用方应短路，不再真调研。
    """
    try:
        raw = _client().get(_cache_key(topic))
    except Exception as e:  # noqa: BLE001 - Redis 不可用降级
        logger.warning("Redis 不可用，跳过缓存读取: %s", type(e).__name__)
        return None
    if not raw:
        return None
    data = json.loads(raw)
    if data.get("__empty"):
        return {}  # 空值缓存命中（防穿透）
    return data


def cache_set(
    topic: str,
    result: dict,
    base_ttl: int | None = None,
    jitter: int | None = None,
) -> None:
    """写缓存。TTL = base + random(0, jitter)——加抖动让各主题过期时刻错开，防雪崩。"""
    ttl = (base_ttl or settings.cache_ttl_base) + random.randint(
        0, jitter or settings.cache_ttl_jitter
    )
    try:
        _client().set(_cache_key(topic), json.dumps(result, ensure_ascii=False), ex=ttl)
    except Exception as e:  # noqa: BLE001
        logger.warning("Redis 不可用，跳过缓存写入: %s", type(e).__name__)


def cache_set_empty(topic: str) -> None:
    """空值缓存（防穿透）：无结果主题写短 TTL 标记，避免反复真调研烧 token。"""
    try:
        _client().set(
            _cache_key(topic),
            json.dumps({"__empty": True}),
            ex=settings.cache_empty_ttl,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Redis 不可用，跳过空值缓存写入: %s", type(e).__name__)


def acquire_rebuild_lock(topic: str) -> bool:
    """抢重建锁（防击穿）。SET NX EX：同一主题并发时只有一个能抢到锁去真调研。

    其余请求等它重建完读缓存；锁带 TTL 自动过期，重建方异常退出也不会死锁。
    """
    try:
        return bool(
            _client().set(_lock_key(topic), "1", nx=True, ex=settings.cache_lock_ttl)
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Redis 不可用，跳过加锁: %s", type(e).__name__)
        return True  # 降级放行：拿不到锁就人人可重建，最坏多跑一次


def release_rebuild_lock(topic: str) -> None:
    try:
        _client().delete(_lock_key(topic))
    except Exception as e:  # noqa: BLE001
        logger.warning("Redis 不可用，跳过释放锁: %s", type(e).__name__)

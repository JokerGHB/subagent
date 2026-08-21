"""Redis 缓存层测试：用 fakeredis（纯 Python 模拟，不依赖真 Redis）。

覆盖三大防护：穿透（空值缓存）、击穿（互斥锁）、雪崩（TTL 抖动），
以及最重要的——Redis 不可用时优雅降级不崩。
"""
import fakeredis
import pytest

from app.storage import cache
from config.settings import settings


@pytest.fixture
def fake_redis(monkeypatch):
    """每个测试用独立的 fakeredis 实例替换模块级客户端，天然隔离。"""
    client = fakeredis.FakeRedis.from_url(
        "redis://localhost:6379/0", decode_responses=True
    )
    monkeypatch.setattr(cache, "_redis_client", client)
    return client


# ---------- 基础 ----------


def test_normalize_topic():
    assert cache.normalize_topic("AI 市场规模") == "ai市场规模"
    assert cache.normalize_topic("AI 市场规模 ") == "ai市场规模"


def test_cache_miss_returns_none(fake_redis):
    assert cache.cache_get("没缓存过的主题") is None


def test_cache_set_get_roundtrip(fake_redis):
    result = {"topic": "AI 市场规模", "status": "written", "key_points": []}
    cache.cache_set("AI 市场规模", result)
    assert cache.cache_get("AI 市场规模") == result
    # 归一化后不同写法也能命中同一缓存
    assert cache.cache_get("ai市场规模") == result


# ---------- 防雪崩：TTL 抖动 ----------


def test_cache_ttl_has_jitter(fake_redis, monkeypatch):
    # 缩小范围便于断言：TTL 应落在 [base, base+jitter] 内
    monkeypatch.setattr(settings, "cache_ttl_base", 100)
    monkeypatch.setattr(settings, "cache_ttl_jitter", 50)
    cache.cache_set("主题", {"topic": "主题"})
    ttl = fake_redis.ttl(cache._cache_key("主题"))
    assert 0 < ttl <= 150  # base 100 + jitter 50


# ---------- 防穿透：空值缓存 ----------


def test_empty_cache_hit_returns_emptydict(fake_redis):
    # 空值缓存命中：返回 {}（区别于未命中的 None，调用方据此短路）
    cache.cache_set_empty("查无结果的主题")
    assert cache.cache_get("查无结果的主题") == {}


# ---------- 防击穿：互斥锁 ----------


def test_lock_mutual_exclusion(fake_redis):
    assert cache.acquire_rebuild_lock("热点主题") is True   # 第一个抢到
    assert cache.acquire_rebuild_lock("热点主题") is False  # 第二个抢不到
    assert cache.acquire_rebuild_lock("别的主题") is True   # 不同主题不受影响
    cache.release_rebuild_lock("热点主题")
    assert cache.acquire_rebuild_lock("热点主题") is True   # 释放后可再抢


# ---------- 降级：Redis 不可用 ----------


def test_redis_down_degrades_gracefully(monkeypatch):
    def broken_client():
        raise ConnectionError("Redis 连不上")

    monkeypatch.setattr(cache, "_client", broken_client)
    # 读取降级为未命中，不抛异常
    assert cache.cache_get("任意主题") is None
    # 写入降级为静默跳过
    cache.cache_set("任意主题", {"topic": "任意主题"})
    cache.cache_set_empty("任意主题")
    # 加锁降级为放行（人人可重建，最坏多跑一次）
    assert cache.acquire_rebuild_lock("任意主题") is True
    cache.release_rebuild_lock("任意主题")  # 不抛

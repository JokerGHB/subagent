"""轻量去重 + 来源可信度评分。

学习点：
- 这是整个系统最「工程」的部分之一：搜索结果大量重复，去重做不好，
  后面抽取/分析全在重复数据上做无用功。
- 纯函数、无外部依赖 —— 所以能用单元测试锁住行为（见 tests/test_dedup.py）。
"""
import re
from urllib.parse import urlparse

# 高可信域名（示例，可扩展）：政务 / 权威机构
HIGH_CRED_DOMAINS = {
    "gov.cn",
    "gov.com",
    "wikipedia.org",
    "who.int",
    "bloomberg.com",
    "reuters.com",
}

# 低可信域名特征词：营销号 / 聚合站
LOW_CRED_KEYWORDS = ("baijiahao", "toutiao", "zhihu.com/answer")


def url_fingerprint(url: str) -> str:
    """去掉协议、www、尾部斜杠、排序 query 后的稳定指纹，用于 URL 去重。"""
    u = urlparse(url)
    host = (u.netloc or "").lower().replace("www.", "")
    path = (u.path or "").rstrip("/")
    query = "&".join(sorted((u.query or "").split("&"))) if u.query else ""
    return f"{host}{path}" + (f"?{query}" if query else "")


def normalize_title(title: str) -> str:
    """标题归一化：去标点/空白/大小写，用于精确标题去重。"""
    return re.sub(r"[\W_]+", "", title.lower())


def credibility_score(url: str) -> float:
    """按域名给来源可信度打分（0~1）。"""
    host = urlparse(url).netloc.lower().replace("www.", "")
    if host in HIGH_CRED_DOMAINS or any(host.endswith("." + d) for d in HIGH_CRED_DOMAINS):
        return 0.9
    if any(k in host for k in LOW_CRED_KEYWORDS):
        return 0.3
    return 0.6


def dedup_sources(sources: list[dict]) -> list[dict]:
    """按 URL 指纹 + 归一化标题去重，保留可信度高的那条，按可信度降序。"""
    seen_url, seen_title = set(), set()
    ordered = sorted(sources, key=lambda s: s.get("credibility", 0.6), reverse=True)
    result = []
    for s in ordered:
        fp = url_fingerprint(s.get("url", ""))
        t = normalize_title(s.get("title", ""))
        if not fp or fp in seen_url:
            continue
        if t and t in seen_title:
            continue
        seen_url.add(fp)
        if t:
            seen_title.add(t)
        result.append(s)
    return result

"""轻量去重逻辑的单元测试。

运行: uv run pytest tests/ -v
"""
from app.search.dedup import credibility_score, dedup_sources, normalize_title, url_fingerprint


def test_url_fingerprint_normalizes_www_and_slash():
    assert url_fingerprint("https://WWW.Example.com/path/") == "example.com/path"


def test_url_fingerprint_sorts_query():
    assert url_fingerprint("https://example.com/path?a=1&b=2") == url_fingerprint(
        "https://example.com/path?b=2&a=1"
    )


def test_normalize_title_strips_noise():
    assert normalize_title("  Hello, World! ") == "helloworld"


def test_credibility_high_low_medium():
    assert credibility_score("https://www.gov.cn/news") == 0.9
    assert credibility_score("https://example.com/x") == 0.6
    assert credibility_score("https://baijiahao.baidu.com/x") == 0.3


def test_dedup_removes_duplicate_url_and_title():
    sources = [
        {"url": "https://a.com/p", "title": "Same", "credibility": 0.6},
        {"url": "https://a.com/p/", "title": "Same", "credibility": 0.6},  # URL 指纹重复
        {"url": "https://b.com/q", "title": "Same", "credibility": 0.9},   # 标题重复，但可信度更高
        {"url": "https://c.com/r", "title": "Unique", "credibility": 0.6},
    ]
    deduped = dedup_sources(sources)
    assert len(deduped) == 2
    # 高可信度的那条应该被保留
    assert deduped[0]["url"] == "https://b.com/q"

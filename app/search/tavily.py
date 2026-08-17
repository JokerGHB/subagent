"""Tavily 搜索客户端（免费档 1000 credits/月）。

学习点：
- 把外部 API 封装成小模块，其它代码只调 search(query)，不关心实现。
- 没配 key 时给出明确的报错提示，而不是一堆看不懂的 HTTP 异常。
"""
from tavily import TavilyClient

from config.settings import settings

_client = TavilyClient(api_key=settings.tavily_api_key)


def search(query: str, max_results: int | None = None) -> list[dict]:
    if not settings.tavily_api_key or settings.tavily_api_key.startswith("tvly-填"):
        raise ValueError(
            "请先在 config/.env 配置 TAVILY_API_KEY\n"
            "（https://app.tavily.com 注册，免费复制 API Key）"
        )
    result = _client.search(
        query=query,
        max_results=max_results or settings.tavily_max_results,
    )
    return result.get("results", [])

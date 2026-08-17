"""网页正文抽取（静态页，轻量）——P2 供 extractor 使用。

学习点：
- trafilatura 能自动剥离导航/广告，只留正文，比 requests + BeautifulSoup
  手写解析省事得多。
- P1 阶段 Tavily 的 content 片段已够用，这里先备好模块，不接进流程。
"""
import trafilatura


def fetch_page_text(url: str) -> str | None:
    """抓取并返回网页正文文本；失败返回 None（调用方自行处理降级）。"""
    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        return None
    return trafilatura.extract(downloaded)

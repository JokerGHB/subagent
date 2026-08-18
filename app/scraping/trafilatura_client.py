"""网页正文抽取（静态页，轻量）——P2 供 extractor 使用。

学习点：
- trafilatura 能自动剥离导航/广告，只留正文，比 requests + BeautifulSoup
  手写解析省事得多。
- 网络抓取是免费的，失败值得重试一次；但注意：LLM 调用是烧钱的，
  绝不能在 extractor 里对 llm.invoke 做同样的重试。
"""
import logging
import time

import trafilatura

logger = logging.getLogger("research.scraping")


def fetch_page_text(url: str, retries: int = 1) -> str | None:
    """抓取并返回网页正文文本；失败重试 retries 次（短退避），仍失败返回 None。

    网络超时/瞬时抖动很常见，免费重试一次能把「整页白抓」的损失救回来。
    """
    for attempt in range(retries + 1):
        downloaded: str | None = None
        try:
            downloaded = trafilatura.fetch_url(url)
        except Exception as e:  # noqa: BLE001 - 网络异常类型杂，统一兜底
            logger.warning("抓取异常 %s (第 %s 次): %s", url[:50], attempt + 1, type(e).__name__)
        if downloaded:
            try:
                return trafilatura.extract(downloaded)
            except Exception as e:  # noqa: BLE001
                logger.warning("正文解析失败 %s: %s", url[:50], type(e).__name__)
        if attempt < retries:
            time.sleep(0.5)
    return None

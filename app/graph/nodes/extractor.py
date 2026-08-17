"""信息抽取 Agent：对单个来源调用模型，抽取强类型事实。

学习点：
- with_structured_output(PydanticModel)：让模型按 schema 返回，拿到的是
  Pydantic 对象而非自由文本 —— 这就是「结构化输出」，比正则/解析可靠得多。
- extractor 也走 Send 并行（一个来源一个分支），和 searcher 同理。
- 抽取失败或内容为空时优雅降级返回空列表，不拖垮整个流程。
"""
import logging

from app.graph.prompts import EXTRACTOR_PROMPT
from app.graph.state import ExtractorInput, Source
from app.models.llm import get_extractor_llm
from app.models.schemas import ExtractionResult
from app.scraping.trafilatura_client import fetch_page_text

logger = logging.getLogger("research.nodes.extractor")


def _get_content(source: Source) -> str:
    """优先用 Tavily 返回的 snippet；太短则尝试抓取正文（最多 2000 字）。"""
    text = source.get("snippet", "")
    if len(text) < 100:
        fetched = fetch_page_text(source["url"])
        if fetched:
            return fetched[:2000]
    return text[:2000]


def extractor_node(state: ExtractorInput) -> dict:
    source = state["source"]
    content = _get_content(source)
    if not content:
        logger.info("无内容可抽取: %s", source["url"][:40])
        return {"facts": []}

    llm = get_extractor_llm().with_structured_output(ExtractionResult)
    prompt = EXTRACTOR_PROMPT.format(
        title=source.get("title", ""),
        url=source["url"],
        content=content,
    )
    try:
        result: ExtractionResult = llm.invoke(prompt)
    except Exception as e:  # noqa: BLE001 - 边界容错：任何模型/网络错误都不拖垮整个流程
        logger.warning("抽取失败 %s: %s", source["url"][:40], type(e).__name__)
        return {"facts": []}

    for f in result.facts:
        f.source_url = source["url"]
    logger.info("%s 抽取 %s 条事实", source["url"][:40], len(result.facts))
    # 注意：这里只返回 facts，不碰 status —— status 是 LastValue 通道，
    # 并行分支同一轮都写它会报错（InvalidUpdateError）。进度标记只由
    # 汇聚点节点（merge/analyzer）更新。
    return {"facts": result.facts}

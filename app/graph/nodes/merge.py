"""汇总节点：所有并行 searcher 完成后执行一次，做全局去重。

学习点：LangGraph 里并行分支（Send 扇出）结束后，汇合节点只会执行一次，
且能看到全部已归约的状态——所以这里能看到所有 searcher 拼出来的完整 sources。
去重是 per-searcher 做不到的（单分支看不到别分支的结果），必须在汇合点做。
"""
import logging

from app.graph.state import Replace, ResearchState
from app.search.dedup import dedup_sources

logger = logging.getLogger("research.nodes.merge")


def merge_node(state: ResearchState) -> dict:
    before = len(state["sources"])
    deduped = dedup_sources(state["sources"])
    removed = before - len(deduped)
    logger.info("全局去重: %s 条 -> %s 条（剔除 %s 条跨子任务重复）", before, len(deduped), removed)
    # Replace 包裹：整体替换而不是追加
    return {"sources": Replace(deduped), "status": "searched"}

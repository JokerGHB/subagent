"""搜索 Agent：对单个子任务调用 Tavily 搜索，再做轻量去重。

学习点：
- 这个节点会被 LangGraph 的 Send 并行调用多次，每次只处理一个子任务，
  返回的 sources 靠 reducer 拼回全局状态——"各干各的，结果汇总"。
- Tavily 免费档返回的 content 片段 P1 阶段够用，完整抓取留到 P2。
"""
from app.graph.state import ResearchState
from app.search.dedup import credibility_score, dedup_sources
from app.search.tavily import search


def searcher_node(state: ResearchState) -> dict:
    subtask = state["subtask"]
    query = subtask["topic"]
    print(f"[searcher] 搜索子任务[{subtask['id']}]: {query}")

    results = search(query)
    sources = [
        {
            "subtask_id": subtask["id"],
            "url": r.get("url", ""),
            "title": r.get("title", ""),
            "snippet": r.get("content", ""),
            "credibility": credibility_score(r.get("url", "")),
        }
        for r in results
        if r.get("url")
    ]

    deduped = dedup_sources(sources)
    print(f"[searcher] 去重后 {len(deduped)}/{len(sources)} 条来源")
    return {"sources": deduped}

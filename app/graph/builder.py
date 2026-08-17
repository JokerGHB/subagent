"""P3 图：规划 → 并行搜索 → 全局去重 → 并行抽取 → 数据分析。

完整链路：
  planner（模型拆任务）
   → Send 扇出 searcher×N（Tavily 搜索 + 局部去重）
   → merge（全局去重）
   → Send 扇出 extractor×M（每来源结构化抽取）
   → analyzer（对全部事实交叉验证、找共识）
   → END

学习点：
- 两级 Send 扇出：任务级（按子任务并行搜索）+ 来源级（按来源并行抽取），
  这是「Map → Reduce → Map」的流水线。
- analyzer 是扇出后的隐式汇聚点：extractor 分支全跑完才会进它，
  所以它能看到完整 facts（LangGraph 自带 join）。
"""
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from app.graph.nodes.analyzer import analyzer_node
from app.graph.nodes.extractor import extractor_node
from app.graph.nodes.merge import merge_node
from app.graph.nodes.planner import planner_node
from app.graph.nodes.searcher import searcher_node
from app.graph.state import ResearchState


def fan_out_searchers(state: ResearchState) -> list[Send]:
    """把每个子任务并行发送给 searcher 节点。"""
    return [Send("searcher", {"subtask": st}) for st in state["subtasks"]]


def fan_out_extractors(state: ResearchState) -> list[Send]:
    """把每条去重后的来源并行发送给 extractor 节点。"""
    return [Send("extractor", {"source": s}) for s in state["sources"]]


def build_initial_state(topic: str) -> dict:
    """构造图的初始状态。CLI（run.py）和 MCP server 共用，避免两处手写漂移。"""
    return {
        "topic": topic,
        "subtasks": [],
        "sources": [],
        "facts": [],
        "key_points": [],
        "status": "",
        "report": "",
    }


builder = StateGraph(ResearchState)
builder.add_node("planner", planner_node)
builder.add_node("searcher", searcher_node)
builder.add_node("merge", merge_node)
builder.add_node("extractor", extractor_node)
builder.add_node("analyzer", analyzer_node)

builder.add_edge(START, "planner")
builder.add_conditional_edges("planner", fan_out_searchers, ["searcher"])
builder.add_edge("searcher", "merge")
builder.add_conditional_edges("merge", fan_out_extractors, ["extractor"])
builder.add_edge("extractor", "analyzer")
builder.add_edge("analyzer", END)

graph = builder.compile()

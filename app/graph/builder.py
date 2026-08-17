"""P1 图：planner（模型拆任务）→ Send 并行扇出 searcher（Tavily + 去重）。

学习点（并行扇出 = 多 Agent 最核心的能力）：
- Send("节点名", 子状态) 让同一个节点被并行调用多次，每个拿到自己的子任务。
- 每个 searcher 返回的 sources 靠 state 里的 reducer 拼回全局——这就是
  MapReduce 的 Map 阶段。
"""
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from app.graph.nodes.merge import merge_node
from app.graph.nodes.planner import planner_node
from app.graph.nodes.searcher import searcher_node
from app.graph.state import ResearchState


def fan_out_searchers(state: ResearchState) -> list[Send]:
    """把每个子任务并行发送给 searcher 节点。"""
    return [Send("searcher", {"subtask": st}) for st in state["subtasks"]]


builder = StateGraph(ResearchState)
builder.add_node("planner", planner_node)
builder.add_node("searcher", searcher_node)
builder.add_node("merge", merge_node)
builder.add_edge(START, "planner")
builder.add_conditional_edges("planner", fan_out_searchers, ["searcher"])
builder.add_edge("searcher", "merge")
builder.add_edge("merge", END)

graph = builder.compile()

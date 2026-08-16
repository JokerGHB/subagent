"""P0 最小图：planner → searcher 串行链路，验证工具链能跑通。

学习要点：
- StateGraph 是「图」：add_node 注册节点函数，add_edge 连线。
- 节点函数签名: (state) -> dict，返回的 dict 会更新全局状态。
- START / END 是特殊节点，标明入口和出口。
- P0 故意不接真实搜索和模型，只打印，保证无 API Key 也能跑。
"""
from langgraph.graph import END, START, StateGraph

from app.graph.state import ResearchState, Subtask


def planner_node(state: ResearchState) -> dict:
    topic = state["topic"]
    print(f"[planner] 收到调研主题: {topic}")
    # 先固定拆一个子任务；P1 会用模型动态拆分
    subtask: Subtask = {"id": "1", "topic": topic, "intent": "市场概览"}
    return {"subtasks": [subtask], "status": "planned"}


def searcher_node(state: ResearchState) -> dict:
    print(f"[searcher] 待搜索子任务数: {len(state['subtasks'])}")
    print("[searcher] 即将调用 Tavily（P1 接入真实搜索）...")
    return {"status": "searched"}


builder = StateGraph(ResearchState)
builder.add_node("planner", planner_node)
builder.add_node("searcher", searcher_node)
builder.add_edge(START, "planner")
builder.add_edge("planner", "searcher")
builder.add_edge("searcher", END)

# compile() 把图变成可调用对象
graph = builder.compile()

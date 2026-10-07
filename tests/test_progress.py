"""进度估算单元测试：纯逻辑 + 一条「LangGraph 流式形状」回归测试（零 LLM 调用）。

分两部分：
1. ProgressTracker 吃真实形状的事件序列，断言阶段推进、percent 单调不降、
   ETA 单调不增、终态 100%。
2. 用假节点建个迷你图，跑一次 stream，把 langgraph 的**事件形状**钉住：
   yield 的是 (mode, chunk) 元组、并行分支各来一条 updates、最后一块 values
   等于最终状态。service._run_graph 依赖这个形状，langgraph 升级改行为要在这里炸。
"""
import time

from app.graph.state import Replace
from app.progress import (
    PHASE_ORDER,
    PHASE_PRIORS,
    ProgressTracker,
    terminal_snapshot,
)


class FakeClock:
    """可手动推进的时钟：进度测试不能靠 time.sleep 真等。"""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def tick(self, seconds: float) -> None:
        self.now += seconds


def _feed(tracker: ProgressTracker, clock: FakeClock, chunk: dict, advance: float = 1.0):
    """喂一条事件（先推进时钟，模拟节点耗时），返回快照。"""
    clock.tick(advance)
    return tracker.on_event(chunk)


def test_phase_progression_and_labels():
    """planner → searcher×3 → merge → extractor×2 → analyzer → writer 的阶段推进。"""
    clock = FakeClock()
    t = ProgressTracker(clock=clock)

    assert t.snapshot()["stage"] == "planning"

    snap = _feed(t, clock, {"planner": {"subtasks": ["a", "b", "c"]}})
    assert snap["stage"] == "searching" and snap["total"] == 3

    for i in range(3):  # 三个子任务各搜完一条
        snap = _feed(t, clock, {"searcher": {"sources": []}})
        assert snap["done"] == i + 1
        assert snap["label"] == f"正在并行搜索来源 {i + 1}/3"

    # merge 返回 Replace 包装的 sources —— 必须也能数出条数
    snap = _feed(t, clock, {"merge": {"sources": Replace(["u1", "u2"])}})
    assert snap["stage"] == "extracting" and snap["total"] == 2 and snap["done"] == 0

    _feed(t, clock, {"extractor": {"facts": []}})
    assert t.snapshot()["stage"] == "extracting"  # 还没抽完，别提前切阶段

    snap = _feed(t, clock, {"extractor": {"facts": []}})
    # 来源全抽完 → 立刻切 analyzing（analyzer 已经在跑了，事件还没回来）
    assert snap["stage"] == "analyzing"

    snap = _feed(t, clock, {"analyzer": {"key_points": []}})
    assert snap["stage"] == "writing"  # analyzer 跑完 → 正在写报告

    snap = t.finish()
    assert snap["stage"] == "done" and snap["percent"] == 100 and snap["eta"] is None


def test_percent_monotonic_increasing():
    """percent 只涨不跌 —— 每个阶段、阶段内每次事件都断言一遍。"""
    clock = FakeClock()
    t = ProgressTracker(clock=clock)
    seen = [t.snapshot()["percent"]]

    events = [
        {"planner": {"subtasks": ["a", "b"]}},
        {"searcher": {}},
        {"searcher": {}},
        {"merge": {"sources": Replace(["u1", "u2", "u3"])}},
        {"extractor": {}},
        {"extractor": {}},
        {"extractor": {}},
        {"analyzer": {}},
        {"writer": {"report": "# r"}},
    ]
    for ev in events:
        seen.append(_feed(t, clock, ev, advance=3.0)["percent"])
    for _ in range(5):  # 写作阶段只有串行、没有事件，靠时间推进涨
        clock.tick(5)
        seen.append(t.snapshot()["percent"])
    seen.append(t.finish()["percent"])

    assert seen == sorted(seen), seen
    assert seen[0] == 0 and seen[-1] == 100


def test_eta_monotonic_non_increasing():
    """ETA 只降不涨：阶段切换（含前段超时）也不会让「还要等多久」往上跳。"""
    clock = FakeClock()
    t = ProgressTracker(clock=clock)
    seen: list[float] = []

    events = [
        {"planner": {"subtasks": ["a"]}},
        {"searcher": {}},
        {"merge": {"sources": Replace(["u1", "u2"])}},
        {"extractor": {}},
        {"extractor": {}},
        {"analyzer": {}},
    ]
    for ev in events:
        seen.append(_feed(t, clock, ev, advance=4.0)["eta"])
    for _ in range(6):
        clock.tick(7)  # 写作阶段拖长了也不会让 ETA 反弹
        seen.append(t.snapshot()["eta"])

    assert all(eta is not None for eta in seen), seen
    assert seen == sorted(seen, reverse=True), seen
    assert t.finish()["eta"] is None  # 结束就不该再报剩余


def test_eta_and_percent_share_priors():
    """刚开跑时：ETA = 全部阶段先验之和，percent = 0（两者同源，不会互相矛盾）。"""
    clock = FakeClock()
    snap = ProgressTracker(clock=clock).snapshot()
    assert snap["percent"] == 0
    assert snap["eta"] == sum(PHASE_PRIORS.values())


def test_serial_phase_caps_below_full():
    """串行阶段（无 N/M 计数）最多报 90% 权重，超时也不会宣称本阶段已完成。"""
    clock = FakeClock()
    t = ProgressTracker(clock=clock)
    t.on_event({"planner": {"subtasks": ["a"]}})
    t.on_event({"searcher": {}})
    t.on_event({"merge": {"sources": Replace(["u1"])}})
    t.on_event({"extractor": {}})  # → analyzing

    clock.tick(9999)  # 分析阶段远超先验
    p = t.snapshot()
    # planning+searching+extracting 已完成，analyzing 权重最多算 90% → 到不了 100
    assert p["percent"] < 100
    assert p["stage"] == "analyzing"


def test_fanout_phase_keeps_creeping_before_branches_finish():
    """并行扇出阶段：分支一条都没回来时也要靠耗时估计爬升（否则进度条干等十几秒）。"""
    clock = FakeClock()
    t = ProgressTracker(clock=clock)
    t.on_event({"planner": {"subtasks": ["a"]}})
    t.on_event({"searcher": {}})
    t.on_event({"merge": {"sources": Replace([f"u{i}" for i in range(10)])}})

    before = t.snapshot()["percent"]
    clock.tick(14)  # 十个抽取分支并发跑着，一条都还没回来
    after = t.snapshot()["percent"]
    assert t.snapshot()["done"] == 0  # 计数确实是 0（不是靠假造 done）
    assert after > before             # 但进度条仍在爬

    clock.tick(9999)  # 再拖也不会把「抽取」标成完成
    assert t.snapshot()["percent"] < 100


def test_waiting_stage_reports_unknown_eta():
    """没抢到重建锁：报「等待」且 ETA 为 None（不能假装在跑规划）。"""
    snap = ProgressTracker(clock=FakeClock()).mark_waiting()
    assert snap["stage"] == "waiting" and snap["eta"] is None
    assert "等待" in snap["label"] and snap["percent"] < 10


def test_terminal_snapshot_shapes():
    """终态快照：done / failed 都 100%、无 ETA，elapsed 用外部墙钟。"""
    done = terminal_snapshot(12.34, "done")
    failed = terminal_snapshot(7.0, "failed")
    assert done["percent"] == 100 and done["elapsed"] == 12.3 and done["eta"] is None
    assert failed["stage"] == "failed" and failed["label"] == "失败"


def test_on_event_tolerates_empty_chunk():
    """空事件（或 None）不该炸 —— 流式里偶尔有空 chunk。"""
    clock = FakeClock()
    t = ProgressTracker(clock=clock)
    assert t.on_event({})["stage"] == "planning"
    assert t.on_event(None)["stage"] == "planning"


def test_phase_order_matches_priors():
    """PHASE_ORDER 与 PHASE_PRIORS 必须一一对应（percent/eta 都靠这张表）。"""
    assert set(PHASE_ORDER) == set(PHASE_PRIORS)


def test_real_graph_node_names_are_all_handled():
    """真实图的节点名必须都被 tracker 认识——改名会让进度条**静默**卡住不前进。"""
    from app.graph.builder import graph

    nodes = set(graph.get_graph().nodes)
    assert {"planner", "searcher", "merge", "extractor", "analyzer", "writer"} <= nodes

    # 每个节点喂一条事件，确认阶段确实推进（没被 elif 链漏掉）
    t = ProgressTracker(clock=FakeClock())
    t.on_event({"planner": {"subtasks": ["a"]}})
    assert t.stage == "searching"
    t.on_event({"merge": {"sources": ["u"]}})
    assert t.stage == "extracting"
    t.on_event({"extractor": {}})
    assert t.stage == "analyzing"   # 唯一来源抽完 → 分析
    t.on_event({"analyzer": {}})
    assert t.stage == "writing"
    t.on_event({"writer": {"report": "# r"}})
    assert t.stage == "writing"    # 事件已到但重写会重置本阶段计时 → 不该重置


# ---------- LangGraph 流式形状回归（钉住 service._run_graph 的依赖） ----------

def _build_mini_graph():
    """假节点迷你图：planner → Send 扇出 searcher×2 → merge。复刻真实图的结构。"""
    import operator
    from typing import Annotated, TypedDict

    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Send

    class MiniState(TypedDict):
        subtasks: Annotated[list[str], operator.add]
        sources: Annotated[list[str], operator.add]
        facts: Annotated[list[str], operator.add]

    def planner(_s: MiniState) -> dict:
        return {"subtasks": ["t1", "t2"]}

    def fan_out(_s: MiniState) -> list[Send]:
        return [Send("searcher", {}), Send("searcher", {})]

    def searcher(_s: MiniState) -> dict:
        return {"sources": ["s"]}

    def merge(_s: MiniState) -> dict:
        return {"sources": ["u1", "u2"], "facts": []}  # 故意返回普通 list（非 Replace）

    g = StateGraph(MiniState)
    g.add_node("planner", planner)
    g.add_node("searcher", searcher)
    g.add_node("merge", merge)
    g.add_edge(START, "planner")
    g.add_conditional_edges("planner", fan_out, ["searcher"])
    g.add_edge("searcher", "merge")
    g.add_edge("merge", END)
    return g.compile(), MiniState


def test_langgraph_stream_shape_and_equivalence():
    """stream_mode=["updates","values"] 的形状 + 与 invoke 返回值等价。

    这是 service._run_graph 的地基：如果 langgraph 升级后不再是 (mode, chunk)
    元组、或最后一块 values 不等于 invoke 的结果，这里先炸，而不是线上进度条静默失效。
    """
    graph, _ = _build_mini_graph()
    initial = {"subtasks": [], "sources": [], "facts": []}

    updates: list[dict] = []
    last_values = None
    for mode, chunk in graph.stream(initial, stream_mode=["updates", "values"]):
        assert mode in ("updates", "values")
        if mode == "values":
            last_values = chunk
        else:
            updates.append(chunk)

    assert last_values == graph.invoke(initial)  # 最后一块 values 就是最终状态
    assert all(isinstance(u, dict) for u in updates)
    # 并行扇出的每个分支各来一条：searcher 出现两次（不是合并成一条）
    assert sum(1 for u in updates if "searcher" in u) == 2
    assert any("planner" in u for u in updates)


def test_tracker_consumes_real_graph_events():
    """把真实事件序列喂给 tracker，确认节点名→阶段映射没跑偏（含并行分支计数）。"""
    graph, _ = _build_mini_graph()
    tracker = ProgressTracker(clock=time.monotonic)

    stages: list[str] = []
    for mode, chunk in graph.stream(
        {"subtasks": [], "sources": [], "facts": []}, stream_mode=["updates", "values"]
    ):
        if mode == "updates":
            stages.append(tracker.on_event(chunk)["stage"])

    assert stages[0] == "searching"    # planner 完成 → 开始搜索
    assert stages[-1] == "extracting"  # merge 完成 → 开始抽取
    assert tracker.total == 2          # merge 返回 2 条 sources → 抽取阶段计数 2

"""调研进度：把 LangGraph 的流式节点事件翻译成「阶段 + 百分比 + 预计剩余」。

为什么进度要按【阶段耗时先验】加权，而不是按节点个数平均分？
一次调研的耗时分布极不均匀：搜索/抽取是并行扇出（墙钟时间约等于最慢那一个），
分析和写报告是串行且输出长（慢）。若按节点个数平均分，抽取阶段一跑完就冲到 80%，
剩下分析和写作还要再等一分钟，进度条会显得在骗人。

所以 percent 与 eta 用**同一套阶段权重**（PHASE_PRIORS）：百分比表达的就是
「这个阶段通常占整条流水线的多少时间」，两者永不打架、且都单调
（percent 只涨、eta 只降；阶段比先验慢也只是让 eta 停住，不会回涨）。

本模块纯逻辑、不碰网络与文件，可离线单测（注入 clock 即可）。
"""
import time
from collections.abc import Callable
from typing import Any

# 各阶段「通常耗时」先验（秒）。合计 ≈ 100s，与整条流水线 1~3 分钟的量级一致。
# 数值依据实测：writer 用 flash 约 40s（故 writing 拿 35%——它是单次调用里最长的一段），
# analyzer 用 max 一次约 25s，搜索/抽取是并行扇出（墙钟≈最慢那一个）故按 ~15/20s 估。
# 实测偏了就改这一处，percent 与 ETA 会同步跟着变。
PHASE_PRIORS: dict[str, int] = {
    "planning": 6,     # planner 一次结构化输出（flash）
    "searching": 14,   # N 个子任务并行 Tavily
    "extracting": 20,  # M 个来源并行抽取（flash，但并发有上限）
    "analyzing": 25,   # analyzer 串行一次（数字分析，用 max）
    "writing": 35,     # writer 串行一次，输出 800~1000 字（实测 ~40s）
}

PHASE_LABELS: dict[str, str] = {
    "planning": "规划子任务",
    "searching": "并行搜索来源",
    "extracting": "并行抽取事实",
    "analyzing": "交叉分析关键点",
    "writing": "撰写报告",
    "waiting": "等待其他请求完成缓存重建",
    "done": "完成",
    "failed": "失败",
}

# 阶段顺序（percent / eta 都按这个顺序累加权重）
PHASE_ORDER: tuple[str, ...] = ("planning", "searching", "extracting", "analyzing", "writing")

_TOTAL_PRIOR = sum(PHASE_PRIORS.values())

# 单次串行调用的阶段（无「N/M」细分进度）最多报 90%，避免事件还没到就宣称跑完
_SERIAL_PHASE_CAP = 0.9

# 没抢到重建锁、在等别人跑完缓存重建：进度未知，给个很小的占位值
_WAITING_PERCENT = 5


def _size(value: Any) -> int:
    """取「条数」：兼容普通 list 与 Replace 包装（merge 节点返回的是 Replace）。"""
    items = getattr(value, "items", value)  # Replace.items
    try:
        return len(items)
    except TypeError:
        return 0


def terminal_snapshot(elapsed: float, stage: str = "done") -> dict:
    """终态快照（stage="done" 成功 / "failed" 无产出）——进度条走满、不再报 ETA。

    给 API / MCP 层用：它们的 job 生命周期比图更长（还有缓存秒回、等待锁、
    失败落库等图之外的路径），所以由它们按自己的墙钟起点构造终态，
    而不依赖图内 tracker 的最后一条事件。
    """
    return {
        "stage": stage,
        "label": PHASE_LABELS.get(stage, stage),
        "percent": 100,
        "done": 0,
        "total": 0,
        "elapsed": round(max(0.0, elapsed), 1),
        "eta": None,
    }


class ProgressTracker:
    """消费 LangGraph `stream_mode="updates"` 的事件，产出给前端渲染的进度快照。

    事件形状是 `{节点名: 该节点返回值}`；并行扇出的**每个分支各来一条**
    （见 tests/test_progress.py 的流式形状回归测试），所以「已完成 N/共 M」是真数出来的。

    阶段切换的时机很关键 —— updates 事件在节点**跑完**才到，所以要按「下一步该谁跑」
    来切阶段：extractor 全抽完 → 切 analyzing；analyzer 跑完 → 切 writing。
    否则用户会看到「正在交叉分析」却在等写报告（差一格）。
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._start = clock()
        self.stage = "planning"
        self.done = 0    # 当前阶段已完成的分支数
        self.total = 0   # 当前阶段的分支总数（0 = 该阶段无细分进度）
        self._stage_start = self._start

    # ---------- 时间 ----------

    def elapsed(self) -> float:
        """整条流水线已耗时。"""
        return max(0.0, self._clock() - self._start)

    def _stage_elapsed(self) -> float:
        """**当前阶段**已耗时（串行阶段的完成比例要按本阶段算，不能按全程）。"""
        return max(0.0, self._clock() - self._stage_start)

    def _enter(self, stage: str, total: int = 0) -> None:
        self.stage = stage
        self.done = 0
        self.total = total
        self._stage_start = self._clock()

    # ---------- 事件 ----------

    def on_event(self, chunk: dict) -> dict:
        """喂一条 updates 事件（可能同时含多个节点），返回最新快照。"""
        for node, payload in (chunk or {}).items():
            if node == "planner":
                # 规划结束 → 开始搜索；子任务数就是搜索分支总数
                self._enter("searching", _size(payload.get("subtasks")))
            elif node == "searcher":
                self.done += 1            # 每个子任务搜完各来一条
            elif node == "merge":
                # 全局去重后的来源数就是抽取分支总数
                self._enter("extracting", _size(payload.get("sources")))
            elif node == "extractor":
                self.done += 1
                if self.total and self.done >= self.total:
                    self._enter("analyzing")   # 来源全抽完 → 分析开始
            elif node == "analyzer":
                self._enter("writing")         # 分析结束 → 写作开始
            elif node == "writer" and self.stage != "writing":
                # 兜底：若因故没收到 analyzer 事件，别让阶段停在上一步
                self._enter("writing")
        return self.snapshot()

    def mark_waiting(self) -> dict:
        """没抢到重建锁、在等别人的调研写完缓存。"""
        self._enter("waiting")
        return self.snapshot()

    def finish(self) -> dict:
        """调研结束——进度条走满（按本 tracker 记的全程耗时算 elapsed）。"""
        return terminal_snapshot(self.elapsed(), "done")

    # ---------- 估算 ----------

    def _phase_fraction(self) -> float:
        """当前阶段完成比例 0~1：**分支计数**与**耗时估计**取较大者。

        两个估计各自都不减，取 max 后仍然单调，所以 percent 只涨、ETA 只降。

        为什么两种都要：
        - 只有分支计数（并行扇出）：10 个抽取并发跑、一个都还没回来时 `done/total`
          恒为 0 —— 进度条会从 20% 干等到 35%（十几秒纹丝不动），正是要修的那个体验。
        - 只有耗时估计（单次串行调用）：规划/分析/写作没有分支可数，只能靠它。
        """
        frac = 0.0
        if self.total:
            frac = min(1.0, self.done / self.total)
        prior = PHASE_PRIORS.get(self.stage)
        if prior:
            # 上限 0.9：分支/事件没到，就不宣称本阶段已完成
            frac = max(frac, min(_SERIAL_PHASE_CAP, self._stage_elapsed() / prior))
        return frac

    def percent(self) -> int:
        """进度百分比：已完成阶段的权重 + 当前阶段权重 × 当前阶段完成比例。"""
        if self.stage == "done":
            return 100
        if self.stage == "waiting":
            return _WAITING_PERCENT
        idx = PHASE_ORDER.index(self.stage)
        weight = float(sum(PHASE_PRIORS[p] for p in PHASE_ORDER[:idx]))
        weight += PHASE_PRIORS[self.stage] * self._phase_fraction()
        return min(99, round(100 * weight / _TOTAL_PRIOR))

    def eta(self) -> float | None:
        """预计剩余秒数（估算）；已完成 / 等待锁（未知）返回 None。

        当前阶段剩余按先验比例折算，其后所有阶段的先验全算上 —— 与 percent 同源，
        所以「进度 78%」和「还要等 22s」永远对得上。切换阶段时剩余只减不增：
        进入下一阶段时的剩余 = Σ(其后阶段先验)，不会超过上一阶段末尾的估计。
        """
        if self.stage in ("done", "waiting"):
            return None
        idx = PHASE_ORDER.index(self.stage)
        remaining = sum(PHASE_PRIORS[p] for p in PHASE_ORDER[idx + 1 :])
        current = PHASE_PRIORS[self.stage] * (1.0 - self._phase_fraction())
        return max(1.0, round(current + remaining))

    # ---------- 对外快照 ----------

    def label(self) -> str:
        base = PHASE_LABELS.get(self.stage, self.stage)
        if self.total:
            return f"正在{base} {self.done}/{self.total}"
        return f"正在{base}…"

    def snapshot(self) -> dict:
        return {
            "stage": self.stage,
            "label": self.label(),
            "percent": self.percent(),
            "done": self.done,
            "total": self.total,
            "elapsed": round(self.elapsed(), 1),
            "eta": self.eta(),
        }

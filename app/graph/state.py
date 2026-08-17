"""LangGraph 共享状态：图里所有节点读写的数据结构。

学习要点：
- TypedDict 定义状态形状；节点 return 的 dict 会被合并进状态。
- Annotated[..., operator.add] 叫「reducer（归约器）」：
  节点返回新列表时不是覆盖，而是【追加】。
  这是多 Agent 并行的关键——多个搜索节点各自返回结果，最后全被拼进 sources。
"""
import operator
from typing import Annotated, TypedDict


class Replace:
    """整体替换标记：包住列表传给 reducer 表示「替换」而不是「追加」。

    学习点：LangGraph 的 reducer 是纯函数 (current, update) -> new。
    并行节点返回普通 list 表示追加；merge 汇总节点用 Replace 表示整体替换。
    """

    def __init__(self, items: list) -> None:
        self.items = items


def merge_or_append(current: list, update) -> list:
    """sources 的自定义 reducer：普通 list 追加，Replace 整体替换。"""
    if isinstance(update, Replace):
        return update.items
    return current + update


class Subtask(TypedDict):
    """规划 Agent 产出的一个调研子任务。"""
    id: str
    topic: str          # 子主题
    intent: str         # 要搜集什么信息


class SearcherInput(TypedDict):
    """Send 并行扇出时，单个 searcher 收到的载荷（只含自己的子任务）。

    学习点：节点通过 Send 被调用时，收到的不是完整 ResearchState，
    而是你传给 Send 的那个 dict —— 所以每个节点应有自己明确的输入类型，
    而不是笼统地标成 ResearchState。
    """
    subtask: Subtask


class Source(TypedDict):
    """一条搜索来源。"""
    subtask_id: str      # 由哪个子任务搜出
    url: str
    title: str
    snippet: str
    credibility: float   # 域名可信度 0~1


class ResearchState(TypedDict):
    topic: str
    subtasks: Annotated[list[Subtask], operator.add]
    sources: Annotated[list[Source], merge_or_append]
    status: str
    report: str

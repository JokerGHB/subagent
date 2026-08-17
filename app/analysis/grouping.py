"""纯代码的数据预处理：把原始事实按维度分组、打分挑选代表。

学习点：LLM 不擅长精确的机械操作（分组、排序、去重），这些交给代码；
LLM 只负责需要「理解」的部分（合并同义词维度、仲裁数值冲突）。
这就是「代码做确定性的事，模型做理解性的事」的分工。
"""
from app.models.schemas import Fact


def normalize_dimension(dimension: str) -> str:
    """归一化维度名：去掉全部空白、转小写。

    中文维度名里空格本无意义，去掉全部空白才能合并
    "AI市场规模" 和 "AI 市场规模" 这类写法差异。
    """
    return "".join(dimension.split()).lower()


def group_facts_by_dimension(facts: list[Fact]) -> dict[str, list[Fact]]:
    """按归一化后的维度把事实分组，返回 {维度: [事实...]}。"""
    groups: dict[str, list[Fact]] = {}
    for f in facts:
        key = normalize_dimension(f.dimension)
        groups.setdefault(key, []).append(f)
    return groups


def select_top_facts(facts: list[Fact], k: int = 3) -> list[Fact]:
    """取一个维度内置信度最高的前 k 条事实，压缩给分析 Agent 的输入。

    输入须已按维度分好组；这里只按 confidence 降序切片。
    """
    return sorted(facts, key=lambda f: f.confidence, reverse=True)[:k]

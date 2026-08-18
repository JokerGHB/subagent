"""结构化输出 schema（Pydantic）。

学习点：with_structured_output(PydanticModel) 让模型直接返回该模型的实例，
字段完全受控 —— 这就是「强类型输出」，比解析自由文本可靠得多。
"""
from pydantic import BaseModel, Field


class Fact(BaseModel):
    """一条可溯源的事实。"""
    source_url: str = ""     # 来源链接（溯源用）
    dimension: str           # 维度/指标名，如"市场规模"
    value: float             # 纯数值 —— 用 float 类型强制模型不许把单位带进来
    unit: str = ""           # 单位，如"亿美元"、"%"
    time: str = ""           # 时间/年份
    quote: str = ""          # 支持该事实的原文句（溯源）
    confidence: float = 0.5  # 置信度 0~1


class ExtractionResult(BaseModel):
    """单个来源的抽取结果：一组事实。"""
    facts: list[Fact] = Field(default_factory=list)


class KeyPoint(BaseModel):
    """分析 Agent 产出的一个关键数据点（多来源交叉验证后的结论）。"""
    dimension: str            # 维度，如"市场规模"（统一表述）
    value: float              # 共识值（纯数字，float 强制）
    unit: str = ""            # 单位，如"亿元"、"%"
    time: str = ""            # 时间/年份
    source_count: int = 1     # 几条来源互相印证
    sources: list[str] = Field(default_factory=list)  # 印证来源 URL（溯源）
    quote: str = ""           # 最可信来源的原文摘录
    conflict: str = ""        # 冲突/口径差异说明；无冲突填空
    confidence: float = 0.5   # 综合置信度 0~1


class AnalysisResult(BaseModel):
    """分析 Agent 的整体输出：一组关键数据点。"""
    key_points: list[KeyPoint] = Field(default_factory=list)


class MetricScore(BaseModel):
    """评测单个指标的打分。"""
    score: int = Field(ge=1, le=5)  # 1~5，5 最好
    reason: str = ""                # 具体理由，引用数据点佐证


class JudgeResult(BaseModel):
    """评测 Agent 的输出：4 个指标 + 综合评价。

    用四个显式字段而非自由列表，强制模型四个都填，结构稳定。
    """
    relevance: MetricScore          # 相关性：贴合主题
    groundedness: MetricScore       # 可溯源性：有原文+URL 支撑
    completeness: MetricScore       # 覆盖度：关键维度是否齐全
    conflict_handling: MetricScore  # 冲突处理：冲突标注而非硬合并
    overall: str = ""               # 一段综合评价

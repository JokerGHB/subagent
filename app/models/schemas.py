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

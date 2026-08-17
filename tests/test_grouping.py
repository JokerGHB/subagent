"""分组预处理的单元测试：纯函数，不需要 API，跑得快。"""
from app.analysis.grouping import (
    group_facts_by_dimension,
    normalize_dimension,
    select_top_facts,
)
from app.models.schemas import Fact


def test_normalize_dimension():
    assert normalize_dimension(" AI 市场规模 ") == "ai市场规模"


def test_group_facts_by_dimension_merges_variants():
    a = Fact(dimension="AI市场规模", value=100)
    b = Fact(dimension=" AI 市场规模 ", value=200)
    c = Fact(dimension="增长率", value=5, unit="%")
    groups = group_facts_by_dimension([a, b, c])
    assert set(groups) == {"ai市场规模", "增长率"}
    assert len(groups["ai市场规模"]) == 2


def test_select_top_facts_by_confidence():
    lo = Fact(dimension="市场规模", value=100, confidence=0.4)
    hi = Fact(dimension="市场规模", value=300, confidence=0.9)
    mid = Fact(dimension="市场规模", value=200, confidence=0.7)
    assert select_top_facts([lo, hi, mid], k=2) == [hi, mid]

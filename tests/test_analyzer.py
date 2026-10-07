"""analyzer 节点测试：只测降级路径（不调用模型）。

回归背景：analyzer 调用的模型 403 时，异常曾被 logger.warning 吞掉、静默降级成
空 key_points，下游 writer 因此跳过 → 报告为空但状态仍是"完成"。现在降级必须
在 errors 通道留下原因，service 才能据此把这次调研标成失败。
"""
from app.graph.nodes import analyzer
from app.graph.nodes.analyzer import analyzer_node
from app.models.schemas import Fact


def _state(**kw) -> dict:
    base = {
        "topic": "国产大模型市场分析",
        "facts": [Fact(dimension="市场规模", value=294.16, source_url="https://a.com")],
        "sources": [{"url": "https://a.com", "credibility": 0.8}],
    }
    base.update(kw)
    return base


def test_analyzer_no_facts_records_error():
    """无事实可分析 → 记「未抽取到事实数据」（常见于全网抽取全失败）。"""
    out = analyzer_node(_state(facts=[]))
    assert out["key_points"] == []
    assert out["errors"] == ["未抽取到事实数据"]


def test_analyzer_llm_failure_records_error(monkeypatch):
    """模型抛错（如 403 PermissionDenied）→ 降级但不能静默：errors 带原因。"""

    class BoomStructured:
        def invoke(self, *a, **k):
            raise PermissionError("403")

    class BoomLLM:
        def with_structured_output(self, *a, **k):
            return BoomStructured()

    monkeypatch.setattr(analyzer, "get_analyzer_llm", lambda: BoomLLM())
    out = analyzer_node(_state())
    assert out["key_points"] == []
    assert out["errors"] == ["分析失败：PermissionError"]
    assert out["status"] == "analyzed"  # 状态仍是节点级"已分析"，整体失败由 service 判定

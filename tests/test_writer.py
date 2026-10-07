"""writer 节点测试：只测纯函数（_format_key_points / _strip_formatting），不调用模型。"""
from app.graph.nodes import writer
from app.graph.nodes.writer import _format_key_points, _strip_formatting, writer_node
from app.models.schemas import KeyPoint


def _kp(**kw) -> KeyPoint:
    base = {
        "dimension": "市场规模",
        "value": 294.16,
        "unit": "亿元",
        "time": "2024",
        "sources": ["https://a.com"],
    }
    base.update(kw)
    return KeyPoint(**base)


def test_format_key_points_renders_value_unit_sources():
    text = _format_key_points({"key_points": [_kp()]})
    assert "市场规模: 294.16亿元 (2024)" in text
    assert "印证1源" in text
    assert "https://a.com" in text


def test_format_key_points_marks_conflict():
    text = _format_key_points({"key_points": [_kp(conflict="口径不一：a vs b")]})
    assert "⚠️冲突: 口径不一" in text


def test_format_key_points_empty():
    assert _format_key_points({"key_points": []}) == "(无关键数据点)"


def test_strip_formatting_counts_real_words():
    md = "# 标题\n\n2024 年中国大模型市场规模达 294.16 亿元。\n[来源](https://a.com/very-long)"
    # 去掉 URL 与语法符号后，只留中文正文 + 数字
    stripped = _strip_formatting(md)
    assert "https" not in stripped
    assert "市场规模达" in stripped
    assert "294.16" in stripped


def test_strip_formatting_ignores_urls_in_length():
    md = "正文。https://a.com/very-long-url" * 10
    assert len(_strip_formatting(md)) < 100


# ---------- 降级时记失败原因（errors 通道） ----------

def test_writer_node_no_key_points_records_error():
    """无关键点 → 跳过生成，但必须记下原因，不能只降级不吭声。"""
    out = writer_node({"topic": "T", "key_points": []})
    assert out["report"] == ""
    assert out["errors"] == ["无关键数据点（分析阶段未产出）"]


def test_writer_node_llm_failure_records_error(monkeypatch):
    """模型抛错 → report 为空 + errors 记原因，下游据 report 是否为空判失败。"""

    class BoomLLM:
        def invoke(self, *a, **k):
            raise RuntimeError("403")

    monkeypatch.setattr(writer, "get_writer_llm", lambda: BoomLLM())
    out = writer_node({"topic": "T", "key_points": [_kp()]})
    assert out["report"] == ""
    assert out["errors"] == ["报告生成失败：RuntimeError"]

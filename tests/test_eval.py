"""评测模块单元测试：format_key_points 是纯函数，不需要 API。"""
from app.eval.judge import format_key_points


def test_format_key_points_lists_data_with_conflict_flag():
    result = {
        "key_points": [
            {
                "dimension": "市场规模",
                "value": 294.16,
                "unit": "亿元",
                "time": "2024年",
                "source_count": 2,
                "sources": ["https://a.com", "https://b.com"],
                "quote": "市场规模达294亿元",
                "conflict": "",
            },
            {
                "dimension": "复合增长率",
                "value": 54.5,
                "unit": "%",
                "time": "未来五年",
                "source_count": 1,
                "sources": ["https://c.com"],
                "quote": "年复合增长率54.5%",
                "conflict": "口径不一：66.1% vs 54.5%",
            },
        ]
    }
    text = format_key_points(result)
    assert "市场规模: 294.16亿元" in text
    assert "印证2源" in text
    assert "⚠️冲突: 口径不一" in text
    assert "来源: https://a.com" in text


def test_format_key_points_empty():
    assert "无关键数据点" in format_key_points({"key_points": []})

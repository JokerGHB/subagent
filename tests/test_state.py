"""状态归约器（自定义 reducer）单元测试。

运行: uv run pytest tests/ -v
"""
from app.graph.state import Replace, merge_or_append


def test_merge_or_append_appends_plain_list():
    assert merge_or_append(["a"], ["b"]) == ["a", "b"]


def test_merge_or_append_replaces_with_replace():
    assert merge_or_append(["a", "b"], Replace(["c"])) == ["c"]

"""状态归约器（自定义 reducer）单元测试。

运行: uv run pytest tests/ -v
"""
from app.graph.state import Replace, merge_or_append


def test_merge_or_append_appends_plain_list():
    assert merge_or_append(["a"], ["b"]) == ["a", "b"]


def test_merge_or_append_replaces_with_replace():
    assert merge_or_append(["a", "b"], Replace(["c"])) == ["c"]


def test_build_initial_state_declares_all_channels():
    """初始状态要覆盖图里所有通道（含 errors）——漏了会让追加式 reducer 拿不到初值。"""
    from app.graph.builder import build_initial_state

    state = build_initial_state("主题")
    assert state["errors"] == []
    assert state["facts"] == [] and state["sources"] == [] and state["key_points"] == []
    assert state["report"] == "" and state["status"] == ""

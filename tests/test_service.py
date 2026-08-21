"""service 落盘逻辑测试：验证 last_result.json + report.md 双落盘。"""
from pathlib import Path

from app import service


def test_save_result_writes_json_and_report_md(monkeypatch, tmp_path: Path):
    # 把模块级路径常量指向临时目录，避免污染真实 data/
    # （RESULT_FILE/REPORT_FILE 是 import 时按 DATA_DIR 算好的，所以要分别 patch）
    monkeypatch.setattr(service, "RESULT_FILE", tmp_path / "last_result.json")
    monkeypatch.setattr(service, "REPORT_FILE", tmp_path / "report.md")
    result = {
        "topic": "测试主题",
        "status": "written",
        "subtasks": [],
        "sources": [],
        "facts": [],
        "key_points": [],
        "report": "# 测试报告\n\n结论：通过",
    }
    service.save_result(result)

    assert (tmp_path / "last_result.json").exists()
    md = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert md == "# 测试报告\n\n结论：通过"


# ---------- 缓存编排 ----------

from app.models.schemas import Fact, KeyPoint
from app.storage import cache, db


def _fake_result(topic: str = "测试主题") -> dict:
    """构造一个完整的图最终状态（Pydantic 对象与真调研一致）。"""
    return {
        "topic": topic,
        "status": "written",
        "subtasks": [],
        "sources": [],
        "facts": [Fact(dimension="市场规模", value=10.0)],
        "key_points": [
            KeyPoint(dimension="市场规模", value=10.0, sources=["https://a.com"])
        ],
        "report": "# 报告",
    }


def _noop(*args, **kwargs):
    return None


def test_invoke_cache_hit_short_circuits(monkeypatch):
    """缓存命中：图不被调用，结果还原成图状态形状（Pydantic 对象）。"""
    monkeypatch.setattr(cache, "cache_get", lambda t: service.serialize_result(_fake_result()))
    called = {"n": 0}

    def fake_invoke(*a, **k):
        called["n"] += 1
        return _fake_result()

    monkeypatch.setattr(service.graph, "invoke", fake_invoke)

    result = service.invoke_research("测试主题")
    assert called["n"] == 0  # 图根本没跑，秒回
    assert isinstance(result["facts"][0], Fact)  # 还原回 Pydantic，下游属性访问不崩
    assert result["report"] == "# 报告"


def test_invoke_force_ignores_cache(monkeypatch, tmp_path):
    """force=True：跳过缓存读，强制重跑图并走完整落库流程。"""
    monkeypatch.setattr(cache, "cache_get", lambda t: service.serialize_result(_fake_result()))
    monkeypatch.setattr(cache, "acquire_rebuild_lock", lambda t: True)
    monkeypatch.setattr(cache, "release_rebuild_lock", _noop)
    monkeypatch.setattr(cache, "cache_set", _noop)
    monkeypatch.setattr(cache, "cache_set_empty", _noop)
    monkeypatch.setattr(db, "save_research_record", _noop)
    monkeypatch.setattr(service, "RESULT_FILE", tmp_path / "last_result.json")
    monkeypatch.setattr(service, "REPORT_FILE", tmp_path / "report.md")
    called = {"n": 0}

    def fake_invoke(*a, **k):
        called["n"] += 1
        return _fake_result()

    monkeypatch.setattr(service.graph, "invoke", fake_invoke)

    result = service.invoke_research("测试主题", force=True)
    assert called["n"] == 1
    assert result["status"] == "written"


def test_invoke_empty_cache_hit_returns_empty(monkeypatch):
    """空值缓存命中：短路返回空结果，不再打真调研（防穿透）。"""
    monkeypatch.setattr(cache, "cache_get", lambda t: {})
    result = service.invoke_research("测试主题")
    assert result["status"] == "empty"
    assert result["key_points"] == []
    assert result["report"] == ""

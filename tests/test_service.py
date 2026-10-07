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


def test_invoke_passes_user_id_and_research_id_to_save(monkeypatch, tmp_path):
    """真调研路径：user_id / research_id 透传到 save_research_record。"""
    captured = {}

    def spy_save(result, research_id=None, user_id=None):
        captured["user_id"] = user_id
        captured["research_id"] = research_id

    monkeypatch.setattr(cache, "cache_get", lambda t: None)  # 未命中
    monkeypatch.setattr(cache, "acquire_rebuild_lock", lambda t: True)
    monkeypatch.setattr(cache, "release_rebuild_lock", _noop)
    monkeypatch.setattr(cache, "cache_set", _noop)
    monkeypatch.setattr(cache, "cache_set_empty", _noop)
    monkeypatch.setattr(db, "save_research_record", spy_save)
    monkeypatch.setattr(service, "RESULT_FILE", tmp_path / "last_result.json")
    monkeypatch.setattr(service, "REPORT_FILE", tmp_path / "report.md")
    monkeypatch.setattr(service.graph, "invoke", lambda *a, **k: _fake_result())

    service.invoke_research("测试主题", force=True, user_id="u1", research_id="r1")
    assert captured["user_id"] == "u1"
    assert captured["research_id"] == "r1"


def test_invoke_defaults_user_id_none(monkeypatch, tmp_path):
    """不带 user_id/research_id（CLI/MCP 调用）→ 落历史时两者为 None（公共）。"""
    captured = {}

    def spy_save(result, research_id=None, user_id=None):
        captured["user_id"] = user_id
        captured["research_id"] = research_id

    monkeypatch.setattr(cache, "cache_get", lambda t: None)
    monkeypatch.setattr(cache, "acquire_rebuild_lock", lambda t: True)
    monkeypatch.setattr(cache, "release_rebuild_lock", _noop)
    monkeypatch.setattr(cache, "cache_set", _noop)
    monkeypatch.setattr(cache, "cache_set_empty", _noop)
    monkeypatch.setattr(db, "save_research_record", spy_save)
    monkeypatch.setattr(service, "RESULT_FILE", tmp_path / "last_result.json")
    monkeypatch.setattr(service, "REPORT_FILE", tmp_path / "report.md")
    monkeypatch.setattr(service.graph, "invoke", lambda *a, **k: _fake_result())

    service.invoke_research("测试主题")
    assert captured["user_id"] is None
    assert captured["research_id"] is None


# ---------- 失败显式上报 ----------

def _failed_state() -> dict:
    """报告为空的图状态：facts 有、key_points 空、errors 记了原因（analyzer 403 的样子）。"""
    return {
        "topic": "失败主题",
        "status": "written",       # 节点降级后仍写 written —— 正因如此才要靠报告是否为空来判定
        "subtasks": [],
        "sources": [],
        "facts": [Fact(dimension="市场规模", value=10.0)],
        "key_points": [],
        "report": "",
        "errors": ["分析失败：PermissionDeniedError", "无关键数据点（分析阶段未产出）"],
    }


def test_mark_failure_flags_empty_report_with_reasons():
    """报告为空 → status=failed，error 汇总去重后的原因（不再静默当成功）。"""
    out = service._mark_failure(_failed_state())
    assert out["status"] == "failed"
    assert out["error"] == "分析失败：PermissionDeniedError；无关键数据点（分析阶段未产出）"


def test_mark_failure_keeps_success_untouched():
    """有报告 → 不判失败、error 置空（局部降级不拖累整体成功）。"""
    result = _fake_result()
    result["errors"] = ["某来源抽取失败：TimeoutError"]  # 局部降级
    out = service._mark_failure(result)
    assert out["status"] == "written"
    assert out["error"] == ""


def test_mark_failure_without_reasons_uses_fallback():
    """report 空但节点没记原因（如 planner 直接产出空）→ 兜底文案，error 不能为空。"""
    state = _failed_state()
    state["errors"] = []
    out = service._mark_failure(state)
    assert out["status"] == "failed"
    assert out["error"] == "未产出报告"


def test_run_and_store_persists_failure(monkeypatch, tmp_path):
    """真调研路径：无报告 → 落盘/落库的就是失败态（status=failed + error）。"""
    saved = {}

    monkeypatch.setattr(cache, "cache_set", _noop)
    monkeypatch.setattr(cache, "cache_set_empty", _noop)
    monkeypatch.setattr(db, "save_research_record", lambda r, **k: saved.update(r))
    monkeypatch.setattr(service, "RESULT_FILE", tmp_path / "last_result.json")
    monkeypatch.setattr(service, "REPORT_FILE", tmp_path / "report.md")
    monkeypatch.setattr(service.graph, "invoke", lambda *a, **k: _failed_state())

    result = service._run_and_store("失败主题")
    assert result["status"] == "failed"
    assert saved["status"] == "failed"                      # 落库的也是失败态
    assert saved["error"] == result["error"] != ""           # 失败原因一并入库


def test_empty_result_carries_reason():
    """空值缓存命中也是一次「没产出」：必须带理由，前端才能提示而不是显示空白。"""
    out = service._empty_result("某主题")
    assert out["status"] == "empty"
    assert out["error"]  # 非空


def test_serialize_roundtrip_carries_error():
    """序列化/反序列化要带上 error，否则缓存往返丢失败原因。"""
    # 真实流程里 _mark_failure 先跑（把 errors 汇总进 error），之后才序列化
    data = service.serialize_result(service._mark_failure(_failed_state()))
    assert data["error"] == "分析失败：PermissionDeniedError；无关键数据点（分析阶段未产出）"
    assert service.deserialize_result(data)["error"] == data["error"]


# ---------- 进度上报 ----------

from app.graph.state import Replace


def test_run_graph_without_callback_uses_invoke(monkeypatch):
    """不传回调（CLI/MCP）→ 仍走 graph.invoke，行为与加进度前完全一致。"""
    calls = {"invoke": 0}

    def fake_invoke(initial, config=None):
        calls["invoke"] += 1
        return _fake_result()

    def boom(*a, **k):  # 不该走流式
        raise AssertionError("无回调时不应调用 graph.stream")

    monkeypatch.setattr(service.graph, "invoke", fake_invoke)
    monkeypatch.setattr(service.graph, "stream", boom)

    out = service._run_graph("测试主题", {})
    assert calls["invoke"] == 1
    assert out["report"] == "# 报告"


def test_run_graph_with_callback_streams_and_reports(monkeypatch):
    """传回调 → 走 stream：updates 驱动 tracker、回调被调用、返回最后一块 values。"""
    events = [
        ("updates", {"planner": {"subtasks": ["a"]}}),
        ("updates", {"searcher": {}}),
        ("updates", {"merge": {"sources": Replace(["u1", "u2"])}}),
        ("values", {"topic": "测试主题", "report": "中间态"}),  # 中间态不该被返回
        ("updates", {"extractor": {}}),
        ("updates", {"extractor": {}}),
        ("values", _fake_result()),
    ]

    def fake_stream(initial, config=None, stream_mode=None):
        assert stream_mode == ["updates", "values"]
        return iter(events)

    def boom(*a, **k):
        raise AssertionError("有回调时应走 graph.stream 而不是 invoke")

    monkeypatch.setattr(service.graph, "stream", fake_stream)
    monkeypatch.setattr(service.graph, "invoke", boom)

    seen: list[dict] = []
    out = service._run_graph("测试主题", {}, seen.append)

    assert out == _fake_result()  # 最后一块 values == invoke 的返回值
    stages = [s["stage"] for s in seen]
    assert stages[0] == "planning"  # 先推一条初始快照，前端立刻有得显示
    assert "searching" in stages and "extracting" in stages
    assert stages[-1] == "done" and seen[-1]["percent"] == 100
    assert seen[1]["total"] == 1  # planner 报的子任务数透传成搜索阶段总数


def test_wait_or_run_reports_waiting_stage(monkeypatch):
    """没抢到锁的等待者：先推一条 waiting 快照（ETA 未知），不假装在跑规划。"""
    monkeypatch.setattr(cache, "cache_get", lambda t: None)  # 等不到缓存
    monkeypatch.setattr(service, "_LOCK_WAIT_ROUNDS", 1)
    monkeypatch.setattr(service, "_LOCK_WAIT_SECONDS", 0)
    monkeypatch.setattr(service, "_run_and_store", lambda *a, **k: _fake_result())

    seen: list[dict] = []
    out = service._wait_or_run("测试主题", on_progress=seen.append)
    assert out["report"] == "# 报告"  # 等超时后兜底真调研
    assert seen[0]["stage"] == "waiting" and seen[0]["eta"] is None

    # 无回调时不应有任何进度动作（CLI 路径）
    service._wait_or_run("测试主题")
    assert len(seen) == 1

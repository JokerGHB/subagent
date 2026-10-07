"""FastAPI 路由测试：TestClient + monkeypatch 假调研结果，零 LLM 调用。"""
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import api
from app.models.schemas import Fact, KeyPoint
from app.storage import db
from config.settings import settings


def fake_invoke(
    topic: str,
    force: bool = False,
    user_id: str | None = None,
    research_id: str | None = None,
    on_progress=None,
) -> dict:
    """假调研：返回完整图状态形状（Pydantic 对象），不碰任何外部服务。

    签名与 invoke_research 对齐（_run_research 会以 research_id=job_id、
    on_progress=... 调用）。这里顺手推两条进度，验证回调能写进 job["progress"]。
    """
    if on_progress is not None:
        on_progress({"stage": "extracting", "label": "正在并行抽取事实 1/2",
                     "percent": 40, "done": 1, "total": 2, "elapsed": 3.0, "eta": 55.0})
    return {
        "topic": topic,
        "status": "written",
        "subtasks": [],
        "sources": [],
        "facts": [Fact(dimension="市场规模", value=10.0)],
        "key_points": [KeyPoint(dimension="市场规模", value=10.0, sources=["https://a.com"])],
        "report": "# 调研报告\n\n测试结论",
    }


@pytest.fixture
def client(monkeypatch, tmp_path: Path):
    """隔离数据库 + 替换调研入口；TestClient 触发 lifespan 建表。"""
    db.configure(tmp_path / "api_test.db")
    monkeypatch.setattr(api, "invoke_research", fake_invoke)
    with TestClient(api.app) as c:
        yield c


def _wait_done(client, job_id: str, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get(f"/research/{job_id}").json()
        if status["status"] == "done":
            return status
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} 未在 {timeout}s 内完成")


def test_post_research_then_get_result(client):
    resp = client.post("/research", json={"topic": "国产大模型"})
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]

    status = _wait_done(client, job_id)
    assert status["status"] == "done"
    # summary 展开到顶层字段（与 MCP research_get_status 一致）
    assert status["subtasks"] == 0
    assert status["sources"] == 0
    assert status["facts"] == 1
    assert status["key_points"] == 1

    md = client.get(f"/research/{job_id}/result")
    assert md.status_code == 200
    assert "测试结论" in md.text

    js = client.get(f"/research/{job_id}/result?format=json")
    assert js.json()["facts"][0]["dimension"] == "市场规模"


def test_history_from_sqlite(client):
    # 历史来自 SQLite：直接落一条（模拟之前调研过）
    db.save_research_record(fake_invoke("历史主题"))

    history = client.get("/history").json()
    assert len(history) == 1
    assert history[0]["topic"] == "历史主题"
    # 历史点开走 /research/{id}/result（SQLite 分支）
    rid = history[0]["id"]
    md = client.get(f"/research/{rid}/result")
    assert md.status_code == 200
    assert "测试结论" in md.text


def test_unknown_job_404(client):
    assert client.get("/research/nope").status_code == 404
    assert client.get("/research/nope/result").status_code == 404


def test_index_serves_frontend(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "多 Agent 调研系统" in resp.text

    # 抽出的 style.css 必须能通过 /static/ 挂载访问（否则浏览器 404，页面裸奔无样式）
    css = client.get("/static/style.css")
    assert css.status_code == 200
    assert "text/css" in css.headers["content-type"]


def test_cleanup_jobs_removes_old_finished(monkeypatch):
    """job 清理：只删「已结束且超时」的，正在跑的和新完成的保留。"""
    api.RESEARCH_JOBS.clear()
    now = time.time()
    api.RESEARCH_JOBS["old_done"] = {"id": "old_done", "status": "done", "created_at": now - 4000}
    api.RESEARCH_JOBS["new_done"] = {"id": "new_done", "status": "done", "created_at": now}
    api.RESEARCH_JOBS["running"] = {"id": "running", "status": "running", "created_at": now - 4000}

    api._cleanup_jobs()

    assert "old_done" not in api.RESEARCH_JOBS   # 超时已结束 → 删
    assert "new_done" in api.RESEARCH_JOBS       # 刚完成 → 保留
    assert "running" in api.RESEARCH_JOBS        # 还在跑 → 保留


# ---------- 访客归属 / 热门 / 管理员 / 计数 ----------

def test_post_research_passes_user_id(client, monkeypatch):
    """POST /research 带 X-User-Id → 透传到 invoke_research（对齐 job_id）。"""
    captured = {}

    def spy(topic, force=False, user_id=None, research_id=None, on_progress=None):
        captured["user_id"] = user_id
        captured["research_id"] = research_id
        return fake_invoke(topic, force, user_id, research_id, on_progress)

    monkeypatch.setattr(api, "invoke_research", spy)
    resp = client.post(
        "/research", json={"topic": "主题"}, headers={"X-User-Id": "visitor-1"}
    )
    job_id = resp.json()["job_id"]
    _wait_done(client, job_id)
    assert captured["user_id"] == "visitor-1"
    assert captured["research_id"] == job_id  # record id 与 job_id 对齐


def test_history_filters_by_user_id(client):
    """GET /history：带头只返回该访客记录，不带头返回全部（公共视角）。"""
    db.save_research_record(fake_invoke("我的主题"), user_id="visitor-1")
    db.save_research_record(fake_invoke("公共主题"))  # user_id=None

    mine = client.get("/history", headers={"X-User-Id": "visitor-1"}).json()
    assert [r["topic"] for r in mine] == ["我的主题"]

    all_ = client.get("/history").json()
    assert len(all_) == 2


def test_history_hot_ranks_by_view_count(client):
    """GET /history/hot：按访问次数倒序，返回 TopN。"""
    db.save_research_record(fake_invoke("热门A"))
    db.save_research_record(fake_invoke("冷门B"))
    a = db.list_history(limit=100)[0]["id"]  # 最新一条是 冷门B
    b = db.list_history(limit=100)[1]["id"]  # 热门A
    db.increment_view_count(b)
    db.increment_view_count(b)
    db.increment_view_count(a)

    hot = client.get("/history/hot").json()
    assert hot[0]["id"] == b  # 访问 2 次的最前
    assert hot[0]["view_count"] == 2


def test_admin_history_requires_token(client, monkeypatch):
    """管理员鉴权：空 token 403 / 缺失 401 / 错误 401 / 正确 200。"""
    monkeypatch.setattr(settings, "admin_token", "s3cret")
    assert client.get("/admin/history").status_code == 401
    assert client.get("/admin/history", headers={"Authorization": "Bearer wrong"}).status_code == 401
    ok = client.get("/admin/history", headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200
    assert "total" in ok.json() and "items" in ok.json()

    monkeypatch.setattr(settings, "admin_token", "")  # 未配置 → 403，绝不裸奔
    assert client.get("/admin/history", headers={"Authorization": "Bearer s3cret"}).status_code == 403


def test_get_result_increments_view_count(client):
    """打开报告详情 +1：GET /research/{id}/result 两次 → view_count=2。"""
    rid = db.save_research_record(fake_invoke("计数主题"))
    assert client.get(f"/research/{rid}/result").status_code == 200
    assert client.get(f"/research/{rid}/result").status_code == 200
    assert db.get_research(rid)["view_count"] == 2


def test_admin_delete_record(client, monkeypatch):
    """管理员删除：鉴权（401/403）+ 删除成功 + 已删 404 + total 减 1。"""
    monkeypatch.setattr(settings, "admin_token", "s3cret")
    rid = db.save_research_record(fake_invoke("待删主题"))
    auth = {"Authorization": "Bearer s3cret"}

    # 鉴权：无 token 401、错 token 401、空配置 403
    assert client.delete(f"/admin/history/{rid}").status_code == 401
    assert client.delete(
        f"/admin/history/{rid}", headers={"Authorization": "Bearer wrong"}
    ).status_code == 401
    monkeypatch.setattr(settings, "admin_token", "")
    assert client.delete(
        f"/admin/history/{rid}", headers={"Authorization": "Bearer s3cret"}
    ).status_code == 403

    # 正确 token 删除成功
    monkeypatch.setattr(settings, "admin_token", "s3cret")
    assert client.get("/admin/history", headers=auth).json()["total"] == 1
    ok = client.delete(f"/admin/history/{rid}", headers=auth)
    assert ok.status_code == 200
    assert ok.json()["deleted"] == 1
    assert db.get_research(rid) is None
    assert client.get("/admin/history", headers=auth).json()["total"] == 0

    # 已删 → 404
    gone = client.delete(f"/admin/history/{rid}", headers=auth)
    assert gone.status_code == 404


# ---------- 失败显式上报 ----------

def failed_invoke(
    topic: str,
    force: bool = False,
    user_id: str | None = None,
    research_id: str | None = None,
    on_progress=None,
) -> dict:
    """假调研（失败版）：搜索有产出、但分析失败 → 报告为空 + status=failed + error。

    这正是线上 403 的样子：facts 有 29 条，key_points 空，report 空。
    """
    return {
        "topic": topic,
        "status": "failed",
        "subtasks": [],
        "sources": [],
        "facts": [Fact(dimension="市场规模", value=10.0)],
        "key_points": [],
        "report": "",
        "error": "分析失败：PermissionDeniedError",
    }


def _wait_error(client, job_id: str, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get(f"/research/{job_id}").json()
        if status["status"] == "error":
            return status
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} 未在 {timeout}s 内进入 error")


def test_failed_job_reports_error_instead_of_done(client, monkeypatch):
    """报告为空 → job 是 error 且带原因（前端据此提示），绝不谎报 done。"""
    monkeypatch.setattr(api, "invoke_research", failed_invoke)
    resp = client.post("/research", json={"topic": "失败主题"})
    job_id = resp.json()["job_id"]

    status = _wait_error(client, job_id)
    assert "分析失败" in status["error"]
    assert status["facts"] == 1  # 计数仍透出：能看出"搜到了但没分析出"


def test_get_result_for_failed_record_shows_reason(client):
    """失败的 SQLite 记录：点开看失败原因（不是空白），且不计入热门排行。"""
    rid = db.save_research_record(failed_invoke("失败主题"))

    md = client.get(f"/research/{rid}/result")
    assert md.status_code == 200
    assert "未产出报告" in md.text
    assert "分析失败：PermissionDeniedError" in md.text
    assert db.get_research(rid)["view_count"] == 0  # 失败记录不进热门

    # 轮询状态接口对失败记录也如实回 error（而不是 done）
    st = client.get(f"/research/{rid}").json()
    assert st["status"] == "error"
    assert "分析失败" in st["error"]


# ---------- 进度条 ----------

def test_status_carries_done_progress(client):
    """轮询接口带 progress：完成后应为 100% / done / 无 ETA。"""
    job_id = client.post("/research", json={"topic": "进度主题"}).json()["job_id"]
    status = _wait_done(client, job_id)
    p = status["progress"]
    assert p["percent"] == 100 and p["stage"] == "done" and p["eta"] is None
    assert p["elapsed"] >= 0


def test_progress_callback_writes_into_job(client, monkeypatch):
    """service 的进度回调确实写进 job["progress"]（前端就靠它渲染）。"""
    captured = {}

    def capturing(topic, force=False, user_id=None, research_id=None, on_progress=None):
        captured["cb"] = on_progress
        return fake_invoke(topic, force, user_id, research_id, on_progress)

    monkeypatch.setattr(api, "invoke_research", capturing)
    job_id = client.post("/research", json={"topic": "进度主题"}).json()["job_id"]
    _wait_done(client, job_id)

    # 跑完后 API 覆盖成终态；此时再手动喂一条中间快照，验证回调指向的就是这个 job
    captured["cb"](
        {
            "stage": "analyzing",
            "label": "正在交叉分析关键点…",
            "percent": 70,
            "done": 0,
            "total": 0,
            "elapsed": 42.0,
            "eta": 20.0,
        }
    )
    assert api.RESEARCH_JOBS[job_id]["progress"]["percent"] == 70


def test_failed_job_progress_is_failed_stage(client, monkeypatch):
    """失败也把进度条推到终态（前端标红 + 显示失败原因），不能停在半路。"""
    monkeypatch.setattr(api, "invoke_research", failed_invoke)
    job_id = client.post("/research", json={"topic": "失败主题"}).json()["job_id"]
    status = _wait_error(client, job_id)
    assert status["progress"]["stage"] == "failed"
    assert status["progress"]["eta"] is None


def test_history_record_status_has_progress(client):
    """job 已被清理、改从 SQLite 历史取状态时，也要给出终态进度。"""
    rid = db.save_research_record(fake_invoke("历史主题"))
    st = client.get(f"/research/{rid}").json()
    assert st["progress"]["percent"] == 100 and st["progress"]["stage"] == "done"


def test_running_status_advances_stale_progress(client):
    """运行中的旧快照要按已过时间往前推 —— 否则进度条与 ETA 会冻住半分钟。

    复现线上观感：analyzing 卡在 40% / 还需 60s 整整 30 秒不动（那是一次串行 LLM
    调用，中间没有任何节点事件，存下来的快照就一直是事件到达那一刻的值）。
    这里把 progress_at 设成 20 秒前，读到的百分比必须比存的那份高、ETA 更低。
    """
    stale = {
        "stage": "analyzing",
        "label": "正在交叉分析关键点…",
        "percent": 40,
        "done": 0,
        "total": 0,
        "elapsed": 40.0,
        "eta": 60.0,
    }
    api.RESEARCH_JOBS["stalerunning"] = {
        "id": "stalerunning",
        "topic": "运行中主题",
        "status": "running",
        "created_at": time.time() - 60,
        "progress": stale,
        "progress_at": time.time() - 20,
    }
    p = client.get("/research/stalerunning").json()["progress"]
    assert p["stage"] == "analyzing"        # 没有新事件 → 阶段不变
    assert p["percent"] > stale["percent"]  # 但不再是冻住的 40%
    assert p["eta"] < stale["eta"]
    assert p["elapsed"] > stale["elapsed"]  # 「已用时长」继续走
    # 基准快照本身没被改动（推进是纯计算），否则反复轮询会叠加漂移
    assert api.RESEARCH_JOBS["stalerunning"]["progress"] == stale
    api.RESEARCH_JOBS.pop("stalerunning", None)


def test_terminal_progress_is_not_advanced(client):
    """终态快照原样返回：不能把 done 的 100% 推成 99%。"""
    api.RESEARCH_JOBS["donestale"] = {
        "id": "donestale",
        "topic": "已完成主题",
        "status": "done",
        "created_at": time.time() - 60,
        "progress": {"stage": "done", "label": "完成", "percent": 100, "done": 0,
                     "total": 0, "elapsed": 42.0, "eta": None},
        "progress_at": time.time() - 30,
    }
    p = client.get("/research/donestale").json()["progress"]
    assert p["percent"] == 100 and p["stage"] == "done" and p["eta"] is None
    api.RESEARCH_JOBS.pop("donestale", None)

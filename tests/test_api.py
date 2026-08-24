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
) -> dict:
    """假调研：返回完整图状态形状（Pydantic 对象），不碰任何外部服务。

    签名与 invoke_research 对齐（_run_research 会以 research_id=job_id 调用）。
    """
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

    def spy(topic, force=False, user_id=None, research_id=None):
        captured["user_id"] = user_id
        captured["research_id"] = research_id
        return fake_invoke(topic, force, user_id, research_id)

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

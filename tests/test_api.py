"""FastAPI 路由测试：TestClient + monkeypatch 假调研结果，零 LLM 调用。"""
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import api
from app.models.schemas import Fact, KeyPoint
from app.storage import db


def fake_invoke(topic: str, force: bool = False) -> dict:
    """假调研：返回完整图状态形状（Pydantic 对象），不碰任何外部服务。"""
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

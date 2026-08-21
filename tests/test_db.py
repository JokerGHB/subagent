"""SQLite 历史层测试：存读 / 列表 / 详情 / 幂等建表。"""
from pathlib import Path

from app.models.schemas import Fact, KeyPoint
from app.storage import db


def _result(topic: str = "AI 市场规模") -> dict:
    """构造一个含 Pydantic 对象的最小图状态（与真调研形状一致）。"""
    return {
        "topic": topic,
        "status": "written",
        "subtasks": [{"id": "1", "topic": "市场规模", "intent": "找数字"}],
        "sources": [{"subtask_id": "1", "url": "https://a.com", "title": "A", "snippet": "", "credibility": 0.8}],
        "facts": [Fact(dimension="市场规模", value=123.0, unit="亿元", time="2025")],
        "key_points": [KeyPoint(dimension="市场规模", value=123.0, unit="亿元", sources=["https://a.com"])],
        "report": "# 报告\n\n结论",
    }


def test_save_and_list(monkeypatch, tmp_path: Path):
    db.configure(tmp_path / "t.db")
    db.init_db()
    db.save_research_record(_result())

    history = db.list_history()
    assert len(history) == 1
    row = history[0]
    assert row["topic"] == "AI 市场规模"
    assert row["status"] == "written"
    assert row["summary"] == {"subtasks": 1, "sources": 1, "facts": 1, "key_points": 1}
    # 列表不拖 report 正文
    assert "report" not in row


def test_list_orders_newest_first(monkeypatch, tmp_path: Path):
    db.configure(tmp_path / "t.db")
    db.init_db()
    db.save_research_record(_result("主题一"))
    db.save_research_record(_result("主题二"))
    db.save_research_record(_result("主题三"))

    history = db.list_history(limit=2)
    assert [r["topic"] for r in history] == ["主题三", "主题二"]


def test_get_research_full_record(monkeypatch, tmp_path: Path):
    db.configure(tmp_path / "t.db")
    db.init_db()
    rid = db.save_research_record(_result())

    row = db.get_research(rid)
    assert row is not None
    assert row["report"] == "# 报告\n\n结论"
    # JSON 字段反序列化回可读结构
    assert row["facts"][0]["dimension"] == "市场规模"
    assert row["facts"][0]["value"] == 123.0
    assert row["key_points"][0]["sources"] == ["https://a.com"]
    assert row["summary"]["facts"] == 1


def test_get_research_missing(monkeypatch, tmp_path: Path):
    db.configure(tmp_path / "t.db")
    db.init_db()
    assert db.get_research("nope") is None


def test_init_db_idempotent(monkeypatch, tmp_path: Path):
    db.configure(tmp_path / "t.db")
    db.init_db()
    db.init_db()  # 第二次调用不报错
    db.save_research_record(_result())
    assert len(db.list_history()) == 1

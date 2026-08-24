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


def test_prune_history_keeps_newest(monkeypatch, tmp_path: Path):
    db.configure(tmp_path / "t.db")
    db.init_db()
    for i in range(5):
        db.save_research_record(_result(f"主题{i}"))

    deleted = db.prune_history(keep=3)
    assert deleted == 2  # 删掉最旧的 2 条
    history = db.list_history(limit=100)
    assert len(history) == 3
    assert [r["topic"] for r in history] == ["主题4", "主题3", "主题2"]


def test_init_db_idempotent(monkeypatch, tmp_path: Path):
    db.configure(tmp_path / "t.db")
    db.init_db()
    db.init_db()  # 第二次调用不报错
    db.save_research_record(_result())
    assert len(db.list_history()) == 1


# ---------- 访问计数 / 个人归属 / 热门 / 管理员全量 ----------

def test_migration_adds_columns_to_existing_table(monkeypatch, tmp_path: Path):
    """旧表（无新列）→ init_db 幂等补列，旧数据 view_count=0、user_id=None。"""
    db.configure(tmp_path / "t.db")
    # 手工建旧版表 + 插一行旧数据（模拟部署前的库）
    conn = db._get_conn()
    conn.execute(
        """CREATE TABLE research_history (
            id TEXT PRIMARY KEY, topic TEXT NOT NULL, normalized_topic TEXT NOT NULL,
            status TEXT NOT NULL, created_at TEXT NOT NULL, report TEXT,
            facts_json TEXT, key_points_json TEXT, summary TEXT)"""
    )
    conn.execute(
        "INSERT INTO research_history (id, topic, normalized_topic, status, created_at) "
        "VALUES ('old1', '旧主题', '旧主题', 'written', '2026-01-01T00:00:00+00:00')"
    )
    conn.commit()

    db.init_db()  # 触发补列
    row = db.get_research("old1")
    assert row["view_count"] == 0
    assert row["user_id"] is None


def test_increment_view_count(monkeypatch, tmp_path: Path):
    db.configure(tmp_path / "t.db")
    db.init_db()
    rid = db.save_research_record(_result())

    assert db.increment_view_count(rid) == 1
    assert db.increment_view_count(rid) == 1
    assert db.get_research(rid)["view_count"] == 2
    assert db.increment_view_count("nope") == 0  # 不存在返回 0


def test_list_history_filters_by_user_id(monkeypatch, tmp_path: Path):
    db.configure(tmp_path / "t.db")
    db.init_db()
    db.save_research_record(_result("我的主题"), user_id="u1")
    db.save_research_record(_result("别人的主题"), user_id="u2")
    db.save_research_record(_result("公共主题"))

    assert len(db.list_history(user_id="u1")) == 1
    assert [r["topic"] for r in db.list_history(user_id="u1")] == ["我的主题"]
    assert len(db.list_history()) == 3  # 不带 → 全部


def test_list_hot_orders_by_view_count(monkeypatch, tmp_path: Path):
    db.configure(tmp_path / "t.db")
    db.init_db()
    a = db.save_research_record(_result("A"))
    b = db.save_research_record(_result("B"))
    c = db.save_research_record(_result("C"))
    db.increment_view_count(a)
    db.increment_view_count(a)
    db.increment_view_count(b)

    hot = db.list_hot(limit=10)
    assert [r["id"] for r in hot] == [a, b, c]
    assert hot[0]["view_count"] == 2


def test_list_all_pagination_and_filter(monkeypatch, tmp_path: Path):
    db.configure(tmp_path / "t.db")
    db.init_db()
    for i in range(5):
        db.save_research_record(_result(f"AI 主题{i}"))

    page = db.list_all(offset=0, limit=2)
    assert page["total"] == 5
    assert len(page["items"]) == 2
    filtered = db.list_all(topic="AI 主题3")
    assert filtered["total"] == 1
    assert filtered["items"][0]["topic"] == "AI 主题3"
    # 管理员可见 user_id（归属）与 view_count
    assert "user_id" in page["items"][0]
    assert "view_count" in page["items"][0]


def test_save_research_record_stores_user_id(monkeypatch, tmp_path: Path):
    db.configure(tmp_path / "t.db")
    db.init_db()
    rid = db.save_research_record(_result("归属测试"), user_id="u9")
    assert db.get_research(rid)["user_id"] == "u9"


def test_delete_research_record(monkeypatch, tmp_path: Path):
    """管理员删除：删掉后 get/list 都查不到，再删返回 0（不存在幂等）。"""
    db.configure(tmp_path / "t.db")
    db.init_db()
    rid = db.save_research_record(_result("待删主题"))

    assert db.delete_research_record(rid) == 1
    assert db.get_research(rid) is None
    assert len(db.list_history()) == 0  # 列表同步消失
    assert db.delete_research_record(rid) == 0  # 不存在 → 0

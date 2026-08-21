"""SQLite 调研历史持久化层。

学习点：
- 为什么用标准库 sqlite3 而不是 SQLAlchemy？当前规模就一条表、单进程读写，
  引入 ORM 反而多一层心智负担和部署依赖。够用即可，将来要换 PG 再迁移。
- WAL 模式（journal_mode=WAL）：写不阻塞读。FastAPI 多线程下
  "一边写新调研记录、一边查历史列表"不会互相卡住。
- 所有存库的复杂字段（facts/key_points/summary）用 JSON 序列化成一个 TEXT 列，
  不做关系拆分——历史记录是"存档"，不是查询优化的关系数据。
"""
import json
import logging
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.storage.cache import normalize_topic

logger = logging.getLogger("research.storage.db")

# 项目数据目录（与 service.py 保持一致：data/）
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DB_FILE = DATA_DIR / "research.db"

_conn: sqlite3.Connection | None = None


def configure(db_path: Path) -> None:
    """显式指定库文件路径并重设连接（测试时指向临时目录，避免污染真实 data/）。"""
    global _conn, DB_FILE
    DB_FILE = Path(db_path)
    if _conn is not None:
        _conn.close()
        _conn = None


def _get_conn() -> sqlite3.Connection:
    """惰性建连。模块级单连接 + WAL：FastAPI 单进程内读写足够安全。"""
    global _conn
    if _conn is None:
        DATA_DIR.mkdir(exist_ok=True)
        _conn = sqlite3.connect(str(DB_FILE), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
    return _conn


def init_db() -> None:
    """建表（幂等：已存在则跳过）。"""
    conn = _get_conn()
    conn.execute(
        """CREATE TABLE IF NOT EXISTS research_history (
            id               TEXT PRIMARY KEY,
            topic            TEXT NOT NULL,
            normalized_topic TEXT NOT NULL,
            status           TEXT NOT NULL,
            created_at       TEXT NOT NULL,
            report           TEXT,
            facts_json       TEXT,
            key_points_json  TEXT,
            summary          TEXT
        )"""
    )
    conn.commit()


def _to_jsonable(obj):
    """把 Pydantic 对象转成可 JSON 序列化的 dict；纯 dict 原样返回。

    图状态里的 facts/key_points 是 Pydantic 模型列表，JSON 序列化前要 model_dump()。
    """
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return obj


def _json_field(obj) -> str:
    return json.dumps([_to_jsonable(o) for o in obj], ensure_ascii=False)


def _summary(result: dict) -> dict:
    """给历史列表用的轻量统计，避免每次列表接口都拖整个报告正文。"""
    return {
        "subtasks": len(result.get("subtasks", [])),
        "sources": len(result.get("sources", [])),
        "facts": len(result.get("facts", [])),
        "key_points": len(result.get("key_points", [])),
    }


def save_research_record(result: dict, research_id: str | None = None) -> str:
    """调研完成后落一条历史记录，返回记录 id。result 为图最终状态（或序列化结果）dict。"""
    rid = research_id or uuid.uuid4().hex[:12]
    conn = _get_conn()
    conn.execute(
        """INSERT OR REPLACE INTO research_history
           (id, topic, normalized_topic, status, created_at,
            report, facts_json, key_points_json, summary)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            rid,
            result["topic"],
            normalize_topic(result["topic"]),
            result.get("status", ""),
            datetime.now(UTC).isoformat(timespec="seconds"),
            result.get("report", ""),
            _json_field(result.get("facts", [])),
            _json_field(result.get("key_points", [])),
            json.dumps(_summary(result), ensure_ascii=False),
        ),
    )
    conn.commit()
    logger.info("历史已落库 id=%s topic=%s", rid, result["topic"])
    return rid


def list_history(limit: int = 20) -> list[dict]:
    """历史列表（倒序）。不返回 report 正文——列表要轻，报告点开详情再取。"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, topic, status, created_at, summary FROM research_history "
        "ORDER BY created_at DESC, rowid DESC LIMIT ?",
        (limit,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["summary"] = json.loads(d["summary"]) if d["summary"] else {}
        except json.JSONDecodeError:
            d["summary"] = {}
        out.append(d)
    return out


def _loads(raw: str | None):
    """把库里的 JSON 列安全反序列化；空值返回 None，坏数据不崩。"""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def get_research(research_id: str) -> dict | None:
    """取单条完整记录（含 report 正文与结构化数据）；不存在返回 None。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM research_history WHERE id = ?", (research_id,)
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    # 把 JSON 列反序列化成可读结构，并重命名为对外的字段名（facts/key_points）
    d["facts"] = _loads(d.pop("facts_json"))
    d["key_points"] = _loads(d.pop("key_points_json"))
    d["summary"] = _loads(d.get("summary"))
    return d

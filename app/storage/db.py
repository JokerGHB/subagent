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
    """建表 + 幂等迁移（旧库补新列）。

    新库：CREATE TABLE 直接带全列。旧库（无 view_count/user_id）：查 PRAGMA
    table_info 逐个补缺列。旧数据迁移后 view_count=0、user_id=NULL（视为公共）。
    """
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
            summary          TEXT,
            view_count       INTEGER NOT NULL DEFAULT 0,
            user_id          TEXT
        )"""
    )
    # 幂等迁移：现有列名集合里缺哪个就补哪个（ALTER TABLE 无 IF NOT EXISTS）
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(research_history)")}
    if "view_count" not in existing:
        conn.execute(
            "ALTER TABLE research_history ADD COLUMN view_count INTEGER NOT NULL DEFAULT 0"
        )
    if "user_id" not in existing:
        # user_id 可空（NULL = 公共 / CLI / MCP 发起的调研）
        conn.execute("ALTER TABLE research_history ADD COLUMN user_id TEXT")
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


def save_research_record(
    result: dict,
    research_id: str | None = None,
    user_id: str | None = None,
) -> str:
    """调研完成后落一条历史记录，返回记录 id。result 为图最终状态（或序列化结果）dict。

    user_id：归属标识。Web 端访客传浏览器访客 ID（个人历史）；CLI/MCP 不传
    （NULL = 公共）。注意：**不要**在 INSERT 里写 view_count——靠默认值 0，
    否则 INSERT OR REPLACE 复用 id 重存时会把已累计的访问次数重置掉。
    """
    rid = research_id or uuid.uuid4().hex[:12]
    conn = _get_conn()
    conn.execute(
        """INSERT OR REPLACE INTO research_history
           (id, topic, normalized_topic, status, created_at,
            report, facts_json, key_points_json, summary, user_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
            user_id,
        ),
    )
    conn.commit()
    prune_history()  # 每次落库后只保留最近 N 条，防止历史无限增长
    logger.info("历史已落库 id=%s topic=%s", rid, result["topic"])
    return rid


def prune_history(keep: int = 200) -> int:
    """只保留最近 keep 条历史，删除更旧的（防磁盘无限增长）。返回删除条数。"""
    conn = _get_conn()
    cur = conn.execute(
        "DELETE FROM research_history WHERE id IN ("
        "  SELECT id FROM research_history"
        "  ORDER BY created_at DESC, rowid DESC LIMIT -1 OFFSET ?"
        ")",
        (keep,),
    )
    conn.commit()
    if cur.rowcount:
        logger.info("清理历史，保留最近 %s 条，删除 %s 条", keep, cur.rowcount)
    return cur.rowcount


def list_history(limit: int = 20, user_id: str | None = None) -> list[dict]:
    """历史列表（倒序）。不返回 report 正文——列表要轻，报告点开详情再取。

    user_id：有则只返回该用户的记录（个人历史）；None 返回全部（公共视角）。
    """
    conn = _get_conn()
    sql = (
        "SELECT id, topic, status, created_at, summary, view_count "
        "FROM research_history"
    )
    params: tuple = ()
    if user_id is not None:
        sql += " WHERE user_id = ?"
        params = (user_id,)
    sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
    rows = conn.execute(sql, (*params, limit)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["summary"] = json.loads(d["summary"]) if d["summary"] else {}
        except json.JSONDecodeError:
            d["summary"] = {}
        out.append(d)
    return out


def increment_view_count(research_id: str) -> int:
    """报告被打开一次 → 访问次数 +1。返回受影响行数（记录不存在返回 0）。"""
    conn = _get_conn()
    cur = conn.execute(
        "UPDATE research_history SET view_count = view_count + 1 WHERE id = ?",
        (research_id,),
    )
    conn.commit()
    return cur.rowcount


def list_hot(limit: int = 10) -> list[dict]:
    """全站热门排行：按访问次数倒序，其次按创建时间（并列时旧记录在前更稳定）。"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, topic, status, created_at, summary, view_count "
        "FROM research_history "
        "ORDER BY view_count DESC, created_at DESC, rowid DESC LIMIT ?",
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


def list_all(offset: int = 0, limit: int = 20, topic: str | None = None) -> dict:
    """管理员全量查看：分页 + 可选主题模糊过滤，返回 {total, items}。

    items 额外带 user_id（归属：NULL=公共）和 view_count，便于管理端排障。
    """
    conn = _get_conn()
    where, params = "", []
    if topic:
        where = " WHERE topic LIKE ?"
        params = [f"%{topic}%"]
    total = conn.execute(
        f"SELECT COUNT(*) FROM research_history{where}", params
    ).fetchone()[0]
    rows = conn.execute(
        f"SELECT id, topic, status, created_at, summary, view_count, user_id "
        f"FROM research_history{where} "
        "ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        try:
            d["summary"] = json.loads(d["summary"]) if d["summary"] else {}
        except json.JSONDecodeError:
            d["summary"] = {}
        items.append(d)
    return {"total": total, "items": items}


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


def delete_research_record(research_id: str) -> int:
    """管理员删除一条历史记录。返回受影响行数（不存在返回 0）。

    只删 SQLite 这一行：我的历史 / 热门 Top10 / 管理员全量都查同一张表，
    删掉一行三者同时消失。Redis 缓存不清（缓存命中不落历史，与历史表无耦合）。
    """
    conn = _get_conn()
    cur = conn.execute(
        "DELETE FROM research_history WHERE id = ?", (research_id,)
    )
    conn.commit()
    if cur.rowcount:
        logger.info("管理员删除历史 id=%s", research_id)
    return cur.rowcount

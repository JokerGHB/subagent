"""FastAPI HTTP 层：让浏览器 / 外部程序通过 HTTP 调用调研系统。

运行（开发）:
    uv run uvicorn app.api:app --reload --port 8000

学习点：
- 复用了 MCP server 的「异步 job 模式」：POST 立刻返回 job_id，
  前端轮询状态、拿到 done 再取报告。调研要几十秒，不能同步卡 HTTP 请求。
- job 注册表是内存 dict：进程重启丢任务，但历史已落 SQLite 不丢（可接受）。
- /research/{job_id}/result 一个端点同时服务两种来源：
  内存里正在跑/刚跑完的任务 + SQLite 里的历史记录（前端历史列表点开就用它）。
"""
import asyncio
import logging
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.logging_config import setup_logging
from app.service import invoke_research, serialize_result
from app.storage import db
from config.settings import settings

logger = logging.getLogger("research.api")

STATIC_DIR = Path(__file__).resolve().parent / "static"

# 内存任务注册表：job_id -> {id, topic, status, result, summary}（同 MCP server 模式）
RESEARCH_JOBS: dict[str, dict] = {}

# 已结束的 job 保留时长：30 分钟后被清理，防止内存无限增长
_JOB_RETENTION_SECONDS = 1800

# 访客 ID 上限：防止 header 塞超长字符串
_MAX_USER_ID_LEN = 128


def _normalize_user_id(raw: str | None) -> str | None:
    """把请求头 X-User-Id 归一化：空串/空白 → None（公共），超长截断。"""
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None
    return value[:_MAX_USER_ID_LEN]


def _require_admin(request: Request) -> None:
    """管理员接口鉴权：Authorization: Bearer <ADMIN_TOKEN>。

    关键：admin_token 为空必须 403（未配置），不能走「空串==空串」比较——
    否则等于没配置时任何无 token 请求都能通过。比较用 compare_digest 防时序攻击。
    """
    expected = settings.admin_token
    if not expected:
        raise HTTPException(status_code=403, detail="管理员接口未配置（ADMIN_TOKEN 为空）")
    auth = request.headers.get("authorization", "")
    token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""
    if not token or not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="管理员令牌无效或缺失")


def _cleanup_jobs() -> None:
    """清掉「已结束且超时」的旧 job，防止 RESEARCH_JOBS 无限膨胀（内存泄漏）。"""
    now = time.time()
    expired = [
        jid
        for jid, job in RESEARCH_JOBS.items()
        if job["status"] in ("done", "error")
        and now - job.get("created_at", now) > _JOB_RETENTION_SECONDS
    ]
    for jid in expired:
        RESEARCH_JOBS.pop(jid, None)
    if expired:
        logger.info("清理 %s 个已结束的旧任务", len(expired))


class ResearchRequest(BaseModel):
    topic: str = Field(min_length=2, max_length=200, description="调研主题")
    force: bool = Field(default=False, description="true 则忽略缓存强制重新调研")


async def _run_research(
    job_id: str, topic: str, force: bool, user_id: str | None
) -> None:
    """后台跑完整调研流程（阻塞调用丢线程池），完成后写回注册表。

    research_id=job_id：让 SQLite 记录 id 与 job_id 对齐。这样 job 清理后
    凭原 job_id 仍能从历史回退取到记录，且 get_result 计数能正确命中。
    """
    job = RESEARCH_JOBS[job_id]
    try:
        result = await asyncio.to_thread(
            invoke_research, topic, force, user_id, research_id=job_id
        )
        job["status"] = "done"
        job["result"] = result
        job["summary"] = {
            "subtasks": len(result["subtasks"]),
            "sources": len(result["sources"]),
            "facts": len(result["facts"]),
            "key_points": len(result["key_points"]),
        }
        logger.info("调研完成 job=%s topic=%s", job_id, topic)
    except Exception as e:  # noqa: BLE001 - 后台任务兜底，把错误状态写给客户端
        logger.error("调研失败 job=%s: %s", job_id, type(e).__name__)
        job["status"] = "error"
        job["error"] = str(e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时建表（幂等）；关闭时无需清理。"""
    setup_logging()
    db.init_db()
    yield


app = FastAPI(title="Multi-Agent Research API", lifespan=lifespan)

# 静态资源目录（style.css 等）：前端 <link href="/static/style.css"> 引用。
# 不挂载的话 /style.css 会 404 —— FastAPI 只会路由显式声明的路径。
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.post("/research", status_code=202)
async def start_research(req: ResearchRequest, request: Request) -> dict:
    """发起一次调研，立即返回 job_id；用 GET /research/{job_id} 轮询进度。

    user_id 从 X-User-Id 头取（浏览器访客 ID），透传给调研任务用于历史归属。
    """
    _cleanup_jobs()  # 新任务进来前，顺手清掉超时的旧任务
    user_id = _normalize_user_id(request.headers.get("x-user-id"))
    job_id = uuid.uuid4().hex[:12]
    RESEARCH_JOBS[job_id] = {
        "id": job_id,
        "topic": req.topic,
        "status": "running",
        "created_at": time.time(),
        "user_id": user_id,
    }
    asyncio.create_task(_run_research(job_id, req.topic, req.force, user_id))
    logger.info("发起调研 job=%s topic=%s force=%s", job_id, req.topic, req.force)
    return {"job_id": job_id, "status": "running", "topic": req.topic}


@app.get("/research/{job_id}")
async def get_status(job_id: str) -> dict:
    """查询任务状态（running / done / error / not_found）。"""
    job = RESEARCH_JOBS.get(job_id)
    if job is None:
        # 内存里没有 → 可能在 SQLite 历史里（已完成的历史任务）
        record = db.get_research(job_id)
        if record is not None:
            return {"job_id": job_id, "status": "done", "topic": record["topic"]}
        raise HTTPException(status_code=404, detail="任务不存在，job_id 是否正确？")
    resp: dict = {"job_id": job["id"], "status": job["status"], "topic": job["topic"]}
    resp.update(job.get("summary", {}))
    if job.get("error"):
        resp["error"] = job["error"]
    return resp


@app.get("/research/{job_id}/result")
def get_result(
    job_id: str,
    format: str = Query(default="markdown", pattern="^(markdown|json)$"),
):
    """取调研结果：markdown 给人读，json 给程序读。

    优先取内存任务；任务不存在则回退查 SQLite 历史（前端历史列表点开就是走这里）。
    """
    job = RESEARCH_JOBS.get(job_id)
    if job is not None:
        if job["status"] != "done":
            raise HTTPException(status_code=202, detail={"status": job["status"], "tip": "请稍后重试"})
        result = job["result"]
        if format == "json":
            return serialize_result(result)
        return Response(content=result["report"] or "（报告为空）", media_type="text/markdown")

    record = db.get_research(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="任务不存在，job_id 是否正确？")
    # 记录存在 → 访问次数 +1（热门排行依据）。内存 job 分支不计数：
    # 刚跑完第一次取结果不算"回看"，job 清理后从历史点开走这里才计。
    db.increment_view_count(job_id)
    if format == "json":
        return record
    return Response(content=record.get("report") or "（报告为空）", media_type="text/markdown")


@app.get("/history")
def list_history(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[dict]:
    """个人调研历史（倒序，不拖报告正文；点开详情用 GET /research/{id}/result）。

    带 X-User-Id → 只返回该访客的记录；不带 → 返回全部（公共视角，兼容 CLI/旧调用）。
    """
    user_id = _normalize_user_id(request.headers.get("x-user-id"))
    return db.list_history(limit=limit, user_id=user_id)


@app.get("/history/hot")
def hot_history(limit: int = Query(default=10, ge=1, le=50)) -> list[dict]:
    """全站热门排行：按访问次数倒序取 TopN（不足 N 条就几条）。"""
    return db.list_hot(limit=limit)


@app.get("/admin/history")
def admin_history(
    request: Request,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    topic: str | None = Query(default=None, max_length=200),
) -> dict:
    """管理员全量历史：分页 + 可选主题模糊过滤，返回 {total, items}。

    鉴权：Authorization: Bearer <ADMIN_TOKEN>（见 _require_admin）。
    """
    _require_admin(request)
    return db.list_all(offset=offset, limit=limit, topic=topic)


@app.delete("/admin/history/{record_id}")
def admin_delete_record(request: Request, record_id: str) -> dict:
    """管理员删除一条历史记录（鉴权同 /admin/history）。

    只删 SQLite 这一行，我的历史 / 热门 Top10 / 管理员全量同时消失。
    Redis 缓存不清（缓存命中不落历史）。顺手清内存 job：record id 与 job_id
    已对齐，避免记录删了但 30 分钟内旧 job 仍能从内存取到报告。
    """
    _require_admin(request)
    deleted = db.delete_research_record(record_id)
    if deleted == 0:
        raise HTTPException(status_code=404, detail="记录不存在")
    RESEARCH_JOBS.pop(record_id, None)
    return {"deleted": deleted, "id": record_id}


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """前端页面。"""
    return FileResponse(STATIC_DIR / "index.html")

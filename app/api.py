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
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from app.logging_config import setup_logging
from app.service import invoke_research, serialize_result
from app.storage import db

logger = logging.getLogger("research.api")

STATIC_DIR = Path(__file__).resolve().parent / "static"

# 内存任务注册表：job_id -> {id, topic, status, result, summary}（同 MCP server 模式）
RESEARCH_JOBS: dict[str, dict] = {}


class ResearchRequest(BaseModel):
    topic: str = Field(min_length=2, max_length=200, description="调研主题")
    force: bool = Field(default=False, description="true 则忽略缓存强制重新调研")


async def _run_research(job_id: str, topic: str, force: bool) -> None:
    """后台跑完整调研流程（阻塞调用丢线程池），完成后写回注册表。"""
    job = RESEARCH_JOBS[job_id]
    try:
        result = await asyncio.to_thread(invoke_research, topic, force)
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


@app.post("/research", status_code=202)
async def start_research(req: ResearchRequest) -> dict:
    """发起一次调研，立即返回 job_id；用 GET /research/{job_id} 轮询进度。"""
    job_id = uuid.uuid4().hex[:12]
    RESEARCH_JOBS[job_id] = {"id": job_id, "topic": req.topic, "status": "running"}
    asyncio.create_task(_run_research(job_id, req.topic, req.force))
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
    if format == "json":
        return record
    return Response(content=record.get("report") or "（报告为空）", media_type="text/markdown")


@app.get("/history")
def list_history(limit: int = Query(default=20, ge=1, le=100)) -> list[dict]:
    """调研历史列表（倒序，不拖报告正文；点开详情用 GET /research/{id}/result）。"""
    return db.list_history(limit=limit)


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """前端页面。"""
    return FileResponse(STATIC_DIR / "index.html")

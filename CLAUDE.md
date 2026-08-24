# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

多 Agent 自动化调研分析系统（简历项目）。输入主题 → 规划子任务 → 并行搜索 → 结构化抽取 → 交叉分析 → 生成 800-1000 字 Markdown 报告，带来源追踪与冲突标注。技术栈：LangGraph（两级并行扇出）+ 阿里云百炼 Qwen（OpenAI 兼容端点）+ Tavily 搜索 + SQLite 历史 + Redis 缓存 + FastAPI/Docker。

## 常用命令

```bash
uv run ruff check .                 # lint
uv run pytest -q                    # 全部测试
uv run pytest tests/test_db.py -q   # 单个文件
uv run pytest tests/test_db.py::test_prune_history_keeps_newest -q  # 单个测试
uv run python run.py "主题"          # CLI 真调研（会烧 LLM token）
docker compose up -d --build        # 起服务（app + redis），访问 http://localhost:8000
docker compose logs -f app          # 看后端日志（排查 LLM 调用/报错）
```

## 架构（需要读多文件才懂的部分）

**一次调研 = 一条 LangGraph 流水线**（`app/graph/builder.py`）：

```
START → planner（拆子任务）
      → Send 扇出 searcher×N（每个子任务并行搜索）
      → merge（全局去重）
      → Send 扇出 extractor×M（每个来源并行结构化抽取）
      → analyzer（交叉验证出关键点）
      → writer（qwen-flash 写报告）→ END
```

**状态机语义**（`app/graph/state.py`）：`Annotated[list, operator.add]` 是追加式 reducer，并行节点返回的列表会被自动合并；`status` 是 LastValue 通道，**只能由汇聚点节点（merge/analyzer/writer）写**，并行分支写它会 `InvalidUpdateError`。

**统一入口 `app/service.py::invoke_research(topic, force=False)`**：CLI、MCP、FastAPI 三个入口都走它。编排顺序 = 查缓存（命中秒回省 token）→ 抢 Redis 重建锁（防击穿）→ 真调研 → 写文件 + SQLite + Redis。`force=True` 跳过缓存读强制重跑。

**序列化契约**：图状态里的 `facts`/`key_points` 是 Pydantic 对象；`serialize_result` 转纯 dict 存缓存/落盘，`deserialize_result` 还原成 Pydantic——缓存命中返回的 dict 必须反序列化，否则下游属性访问（`kp.conflict`）会崩。

**存储层**（`app/storage/`）：`db.py` 用标准库 sqlite3 + WAL（无 ORM），`save_research_record` 后自动 `prune_history(keep=200)`；`cache.py` 处理 Redis 三大坑——防穿透（空值缓存 5min）、防击穿（SET NX EX 互斥锁）、防雪崩（TTL 抖动 base 24h ± 30min）。**所有外部依赖（Redis/Langfuse）连不上都优雅降级，绝不阻塞主流程**。

**观测**：Langfuse 通过 `_langfuse_callback()` 接入（`app/service.py`），`LANGFUSE_HOST` 必须指向真实地址（云版 `https://cloud.langfuse.com`，默认值 `localhost:3000` 会静默丢 trace）。排查 AI 调用问题先看 `docker compose logs -f app`。

**Web/HTTP 层**（`app/api.py`）：异步 job 模式（POST 立刻返回 job_id，内存 `RESEARCH_JOBS` 30 分钟清理）。历史/热门/管理员接口都查同一张 `research_history` 表——`GET /history` 用 `X-User-Id` 头过滤个人、`GET /history/hot` 按 `view_count` 排行、`GET /admin/history` + `DELETE /admin/history/{id}` 用 `_require_admin`（`Authorization: Bearer <ADMIN_TOKEN>`，空 token 必须 403）。静态资源走 `app.mount("/static", StaticFiles)`，前端 `index.html` 引 `/static/style.css`。

## 关键约束（务必遵守）

- **token 成本是最高优先级**（用户反复强调）：日常验证只跑离线单测（ruff + pytest，零 LLM 调用），**绝不自动跑真调研/评测 E2E**。真调研只在用户明确要求时跑。
- **`config/.env` 含真实密钥**（DASHSCOPE_API_KEY/TAVILY_API_KEY/LANGFUSE/ADMIN_TOKEN 四件套）：gitignored，绝不提交；`.dockerignore` 也排除它。部署靠 `docker-compose.yml` 的 `env_file: ./config/.env` 运行时注入。
- **百炼 max 档 403 无权限**：不要改回 max（账户未开通）。模型分工已定——**writer 用 flash**（实测质量可接受、耗时从 plus 的 ~104s 降到 ~40s）、**judge/analyzer 用 plus**。改模型在 `config/settings.py`。
- **报告默认 800~1000 字**：writer 字数统计已改为「去掉 URL/语法符号的有效正文」，不要用 `len(md)` 直接判断（会把 URL 算进去虚报 2403 字）。
- **不要建立系统内对话记忆**：系统是 MCP 工具，用户记忆由外层 AI（Cursor/Claude）持有；系统只记自己的产出（SQLite 历史 + Redis 缓存）。
- **job 注册表是内存 dict**（`api.py`/`mcp_server.py` 的 `RESEARCH_JOBS`）：已完成任务 30 分钟后清理，别让它们无限增长。

## 踩过的坑

- **MCP stdio 传输**：stdout 是协议通道，所有日志必须走 stderr（`app/logging_config.py`）。
- **FastMCP 参数**：工具参数用扁平关键字 + `Annotated[..., Field(...)]`，单个 Pydantic 模型会被嵌套进 "params" 键。
- **with_structured_output**：返回类型标注不准，用 `cast(Model, llm.invoke(prompt))` 消除 Pylance 假阳性（运行时实际是 Model）。
- **测试隔离**：`db.configure(tmp_path)` 隔离 SQLite；`RESULT_FILE`/`REPORT_FILE` 是 import 时算好的，要分别 monkeypatch；Redis 用 fakeredis 替换模块级 `_redis_client`。
- **`crypto.randomUUID` 仅在安全上下文可用**：公网 IP 走纯 HTTP 时它是 `undefined`，前端 `getVisitorId` 必须降级（`Date.now()`+`Math.random()` 拼唯一 ID）。页面要进安全上下文（HTTPS/localhost）才有一整套安全 API。
- **静态资源不自动路由**：FastAPI 只路由显式声明的路径。`index.html` 抽出的 `style.css` 必须 `app.mount("/static", StaticFiles(...))` 才能访问（link 用 `/static/style.css`）。
- **docker compose 的 `env_file` 只在容器创建时注入**：改 `config/.env`（如 ADMIN_TOKEN）后要 `docker compose up -d`（自动重建）才生效，`docker compose restart` 不会重新读。

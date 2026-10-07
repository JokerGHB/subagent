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
      → writer（写报告）→ END
```

**状态机语义**（`app/graph/state.py`）：`Annotated[list, operator.add]` 是追加式 reducer，并行节点返回的列表会被自动合并；`status` 是 LastValue 通道，**只能由汇聚点节点（merge/analyzer/writer）写**，并行分支写它会 `InvalidUpdateError`。`errors` 也是追加式通道：节点降级返回空结果时必须顺手写一句人话原因（如「分析失败：PermissionDeniedError」），供 service 汇总成对用户可见的失败提示——**降级不能静默**。

**失败判定（`app/service.py::_mark_failure`）**：以「report 是否为空」为准——report 是最终产物，analyzer 失败 → key_points 空 → writer 跳过 → report 必空。报告为空即 `status="failed"` + `error=<汇总原因>`（去重保序），失败态照样落盘/落库（`research_history.error` 列），Web/MCP/CLI 三端都据此显式提示而不是显示「完成」+ 空白。

**统一入口 `app/service.py::invoke_research(topic, force=False)`**：CLI、MCP、FastAPI 三个入口都走它。编排顺序 = 查缓存（命中秒回省 token）→ 抢 Redis 重建锁（防击穿）→ 真调研 → 写文件 + SQLite + Redis。`force=True` 跳过缓存读强制重跑。

**进度上报（`app/progress.py` + `service._run_graph`）**：Web/MCP 传 `on_progress` 回调时，`invoke_research → _run_and_store → _run_graph` 把 `graph.invoke` 换成 `graph.stream(stream_mode=["updates","values"])`——`updates` 事件喂 `ProgressTracker` 产出快照，**最后一块 `values` 就是最终状态**（与 invoke 等价，`tests/test_progress.py` 有流式形状回归测试钉住这个契约）。不传回调时**一律走 invoke**，所以 CLI 与旧测试零改动。`ProgressTracker` 里 `PHASE_PRIORS` 是各阶段耗时先验，**percent 和 eta 共用这一套权重**（所以「78%」与「还需 22s」永不矛盾）；阶段切换靠「下一步该谁跑」判定（extractor 抽完→analyzing、analyzer 跑完→writing），因为 updates 事件是节点**跑完**才到，差一格就会显示错阶段。

**序列化契约**：图状态里的 `facts`/`key_points` 是 Pydantic 对象；`serialize_result` 转纯 dict 存缓存/落盘，`deserialize_result` 还原成 Pydantic——缓存命中返回的 dict 必须反序列化，否则下游属性访问（`kp.conflict`）会崩。

**存储层**（`app/storage/`）：`db.py` 用标准库 sqlite3 + WAL（无 ORM），`save_research_record` 后自动 `prune_history(keep=200)`；一条记录带 `status` / `error`（失败原因）/ `view_count` / `user_id`。`cache.py` 处理 Redis 三大坑——防穿透（空值缓存 5min）、防击穿（SET NX EX 互斥锁）、防雪崩（TTL 抖动 base 24h ± 30min）。**所有外部依赖（Redis/Langfuse）连不上都优雅降级，绝不阻塞主流程**。

**观测**：Langfuse 通过 `_langfuse_callback()` 接入（`app/service.py`），`LANGFUSE_HOST` 必须指向真实地址（云版 `https://cloud.langfuse.com`，默认值 `localhost:3000` 会静默丢 trace）。排查 AI 调用问题先看 `docker compose logs -f app`。

**Web/HTTP 层**（`app/api.py`）：异步 job 模式（POST 立刻返回 job_id，内存 `RESEARCH_JOBS` 30 分钟清理）。`_run_research` 把进度回调写进 `job["progress"]`（初始先塞一条 0% 快照——缓存秒回/等锁这些路径没有图事件，否则前端进度条没得渲染），`GET /research/{job_id}` 透出 `progress`；前端 1.2s 轮询渲染进度条。历史/热门/管理员接口都查同一张 `research_history` 表——`GET /history` 用 `X-User-Id` 头过滤个人、`GET /history/hot` 按 `view_count` 排行、`GET /admin/history` + `DELETE /admin/history/{id}` 用 `_require_admin`（`Authorization: Bearer <ADMIN_TOKEN>`，空 token 必须 403）。静态资源走 `app.mount("/static", StaticFiles)`，前端 `index.html` 引 `/static/style.css`。

## 关键约束（务必遵守）

- **token 成本是最高优先级**（用户反复强调）：日常验证只跑离线单测（ruff + pytest，零 LLM 调用），**绝不自动跑真调研/评测 E2E**。真调研只在用户明确要求时跑。
- **`config/.env` 含真实密钥**（DASHSCOPE_API_KEY/TAVILY_API_KEY/LANGFUSE/ADMIN_TOKEN 四件套）：gitignored，绝不提交；`.dockerignore` 也排除它。部署靠 `docker-compose.yml` 的 `env_file: ./config/.env` 运行时注入。
- **模型分级已定案**（在 `config/settings.py`，只有这里改）：planner/extractor/writer 用 **`qwen3.8-flash`**、analyzer/judge 用 **`qwen3.8-max`**。依据是「调用次数 + 串行还是并行」：extractor 每来源一次是 token 大头走 flash，串行节点（planner/analyzer/writer）每次耗时都直接叠进用户等待，所以 writer 走 flash（实测 max ~104s → flash ~40s，质量可接受）、analyzer 只调 1 次但质量关键故不降级。**曾经「max 档 403」的旧结论已作废**（现在 max 可正常调用）。`llm_searcher` 是死配置（searcher 只走 Tavily，全仓库无人调 `get_searcher_llm`）。`llm_timeout=120` 是必需的：不设超时模型挂起会让任务永远 running、进度条永远卡住。
- **报告默认 800~1000 字**：writer 字数统计已改为「去掉 URL/语法符号的有效正文」，不要用 `len(md)` 直接判断（会把 URL 算进去虚报 2403 字）。
- **失败必须显式上报**：任何节点降级返回空结果时都要写 `errors` 通道；报告为空 = 这次调研失败，`status="failed"` + `error=原因` 落盘落库，前端/CLI/MCP 都要显示原因。**绝不允许「状态=完成、点开是空白」**。
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
- **节点降级吞异常会伪装成成功**：analyzer 的模型 403 曾只 `logger.warning` 一句（无堆栈）就返回空 key_points，writer 见无关键点直接跳过 → report 空、状态却是 `written`，历史里留一条点开空白的记录。修法见上「失败必须显式上报」；排查 AI 调用问题先 `docker compose logs -f app`（现在异常都是 `logger.exception`，带完整堆栈和模型名）。
- **`logger.exception` 会让 `# noqa: BLE001` 变成多余**：ruff 的 blind-except 豁免「用 `logger.exception` 记录堆栈」的处理器，所以把 `logger.warning` 升级成 `logger.exception` 后，原本必需的 `# noqa: BLE001` 会被 RUF100 报 unused（`ruff check` 会红）——此时直接删掉 noqa，别留着。
- **流式事件是「节点跑完」才到，不是「节点开始」**：`stream_mode="updates"` 里 `{'analyzer': ...}` 到达时 analyzer 已结束、writer 正在跑。所以阶段要按「下一步该谁跑」切（analyzer 事件 → stage=writing），否则用户会看到「正在交叉分析」其实在等写报告。串行阶段的完成比例也要按**本阶段**耗时算（`_stage_elapsed()`），用全程 elapsed 会让刚落进 writing 的阶段直接冲到 90%。
- **给 service 加可选回调时别改默认路径**：`_run_graph` 只在 `on_progress` 非 None 时走 `graph.stream`，否则保持 `graph.invoke`——CLI/MCP 与 4 个 patch `graph.invoke` 的测试因此零改动。改这类「加功能」时优先用「可选参数 + 保留原路径」而不是全局替换实现。

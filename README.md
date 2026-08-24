# 多 Agent 自动化调研分析系统

输入一个调研主题 → 自动规划、联网搜索、过滤噪声、抽取数据、交叉验证 → 输出一份**带来源溯源、冲突标注**的 800~1000 字 Markdown 调研报告。

基于 **LangGraph** 的两级并行流水线，集成 **阿里云百炼 Qwen** 分级模型与 **Tavily** 搜索。可作 **CLI** 命令行工具、标准 **MCP 工具**（接入 Claude Desktop / Cursor），也可一键 **Docker 部署成 Web 服务**（FastAPI + 前端，带 SQLite 历史与 Redis 缓存）。

## 特性

- **多 Agent 分工**：规划 → 搜索 → 抽取 → 分析 → 报告，各节点可单独替换。
- **两级并行扇出**（LangGraph `Send`）：子任务并行搜索 → 汇聚去重 → 来源并行抽取，一条调研约 1~3 分钟。
- **防幻觉设计**：每个数据点强制带原文摘录 + 来源 URL；多来源数值冲突时**标注分歧**而非强行合并。
- **强类型约束**：抽取/分析结果用 Pydantic 结构化输出，`value` 强制纯数字——类型约束比提示词更可靠。
- **缓存与记忆**：Redis 同主题缓存命中秒回（省 token，防穿透/击穿/雪崩）；SQLite 持久化历史（自动保留最近 200 条）。
- **可观测**：Langfuse trace 完整记录每次 LLM 调用的 prompt / 回复 / token / 耗时 / 费用。
- **LLM-as-a-Judge 评测**：对产出按可溯源性 / 冲突处理 / 相关性 / 覆盖度打分。
- **多入口**：CLI、MCP、Web（FastAPI + 极简前端）三端复用同一套流水线。

## 系统架构

```
调研主题
  │
  ▼
┌────────────────────────────────┐
│ planner  规划 Agent  qwen-flash │  拆成 2~4 个可并行搜索的子任务
└────────────────────────────────┘
  │  Send 并行扇出
  ▼
┌────────────────────────────────┐
│ searcher 搜索 Agent  Tavily     │  每子任务搜 3 条来源 + 轻量去重
└────────────────────────────────┘
  │
  ▼
merge 汇聚点（跨子任务全局去重）
  │  Send 并行扇出
  ▼
┌────────────────────────────────┐
│ extractor 抽取 Agent qwen-flash │  网页 → 强类型事实（纯数字+单位+原文）
└────────────────────────────────┘
  │
  ▼
┌────────────────────────────────┐
│ analyzer 分析 Agent qwen-plus   │  交叉验证 + 冲突标注 + 找共识
└────────────────────────────────┘
  │
  ▼
┌────────────────────────────────┐
│ writer  报告 Agent  qwen-plus   │  组装 800~1000 字 Markdown 报告
└────────────────────────────────┘
  │
  ▼
报告落盘 + SQLite 历史 + Redis 缓存 + Langfuse trace
```

两级 `Send` 并行扇出（Map-Reduce-Map）：子任务并行搜索 → 汇聚去重 → 来源并行抽取 → 汇聚分析。只有汇聚点节点写全局进度，避免并行分支对同一状态通道重复写入。

## Agent 角色与模型分级

| Agent          | 模型          | 职责                                                |
| -------------- | ------------- | --------------------------------------------------- |
| 规划 planner   | qwen3.7-flash | 把主题拆成 2~4 个子任务                             |
| 搜索 searcher  | Tavily API    | 联网搜索 + 来源可信度打分 + 去重                    |
| 抽取 extractor | qwen3.7-flash | 网页 → 结构化事实（value 纯数字 / unit / 原文摘录） |
| 分析 analyzer  | qwen3.7-plus  | 同维度合并、冲突标注、丢弃噪声，输出关键数据点      |
| 报告 writer    | qwen3.7-plus  | 按 800~1000 字要求组装 Markdown 报告                |
| 评测 judge     | qwen3.7-plus  | 按可溯源性/冲突处理/相关性/覆盖度打分               |

按任务难度分级用模型：flash 干高频重活（搜索/抽取），plus 干关键判断（分析/报告）。> 注：账户未开通 max 档（调用返回 403），所以 writer/judge 用 plus；开通后可在 `config/settings.py` 改回 `qwen3.7-max`。

## 快速开始

### 本地 CLI

```bash
uv sync                              # 安装依赖
cp config/.env.example config/.env   # 配置密钥（.env 已被 gitignore）
uv run python run.py "国产大模型市场规模"   # 跑一次调研
uv run ruff check .                  # lint
uv run pytest -q                     # 全部离线单测（零 LLM 调用）
uv run pytest tests/test_db.py::test_prune_history_keeps_newest -q  # 单个测试
```

密钥需填：`DASHSCOPE_API_KEY`（阿里云百炼）、`TAVILY_API_KEY`（Tavily 免费档）。

### Web 服务（Docker 一键起）

```bash
docker compose up -d --build   # 自动创建 app + redis 两个容器
# 浏览器打开 http://localhost:8000
```

### Web 前端页面

前端就是单个 HTML 文件（`app/static/index.html`，原生 JS + fetch，无构建），浏览器打开即可当"测试网页"用，页面分两块：

**① 提交调研卡片**
- 输入框：填调研主题（≥2 字）
- 「强制刷新」复选框：勾上后忽略 Redis 缓存强制重新调研（默认命中缓存直接秒回）
- 点「开始调研」→ 每 3 秒轮询一次进度（显示 `来源 N · 事实 N · 关键点 N`）→ 完成后自动渲染 Markdown 报告

**② 调研历史卡片**
- 倒序展示最近 20 条 SQLite 历史（主题 / 本地时区时间 / 事实数 / 状态标签）
- 点击任意一条回看该次调研的完整报告

前端背后就三个 HTTP 接口：`POST /research` 提交 → 轮询 `GET /research/{job_id}` 看进度 → `GET /research/{job_id}/result` 取报告。报告用极简 Markdown 渲染（标题/列表/加粗/链接），演示够用、不引框架。

## MCP 接入

把调研系统暴露为标准 MCP server，任意 AI 客户端可调用：

```bash
uv run python app/mcp_server.py
```

Claude Desktop / Cursor 中配置（stdio 方式）：

```json
{
  "mcpServers": {
    "research": {
      "command": "uv",
      "args": ["run", "python", "app/mcp_server.py"]
    }
  }
}
```

| 工具                          | 说明                                  |
| ----------------------------- | ------------------------------------- |
| `research_start(topic)`       | 提交调研主题，返回 job_id（异步执行） |
| `research_get_status(job_id)` | 查询调研进度                          |
| `research_get_result(job_id)` | 拉取调研结果（Markdown）              |

## 存储与缓存

- **SQLite 历史**（`app/storage/db.py`）：标准库 sqlite3 + WAL，每次调研落一条记录（报告/事实/关键点），自动保留最近 200 条。
- **Redis 缓存**（`app/storage/cache.py`）：同主题 24h（±30min 抖动）秒回省 token，处理三大问题——**防穿透**（无结果主题 5 分钟空值缓存）、**防击穿**（`SET NX EX` 互斥锁）、**防雪崩**（TTL 抖动）。Redis/Langfuse 连不上一律优雅降级，不阻塞主流程。

## 可观测（Langfuse）

在 `config/.env` 配三个变量即可接入：

```ini
LANGFUSE_PUBLIC_KEY=...
LANGFUSE_SECRET_KEY=...
LANGFUSE_HOST=https://cloud.langfuse.com   # 云版；自托管填你的地址
```

配置后每次调研会在 Langfuse 面板生成一棵调用树（planner → searcher → extractor → analyzer → writer），每层可展开看完整 prompt / 回复 / token / 耗时 / 费用。

## 评测（LLM-as-a-Judge）

```bash
uv run python -m scripts.eval_research            # 复用 data/last_result.json，不重跑调研
uv run python -m scripts.eval_research "新主题"   # 现场调研后评测
```

历史实测（早期用 max 打分，平均 4.0/5）：可溯源性 5/5、冲突处理 5/5、相关性 3/5、覆盖度 3/5——评测体系能真实暴露短板，不是全满分。

## 成本控制

| 手段                                 | 效果                                              |
| ------------------------------------ | ------------------------------------------------- |
| `tavily_max_results = 3`             | 来源数 ~20 → ~12，抽取调用降 ~40%                 |
| `extractor_max_chars = 1500`         | 限制喂给抽取模型的正文长度                        |
| snippet ≥100 字不抓正文              | 连 HTTP 请求都省                                  |
| Redis 同主题缓存（24h + 抖动）       | 命中直接秒回，0 token 成本                        |
| 空值缓存 + 互斥锁                    | 防穿透/击穿：无结果主题不反复打、热点并发只跑一次 |
| 结果落盘复用                         | 评测不重跑 3 分钟调研                             |
| 抓取失败免费重试（LLM 调用绝不重试） | 救回「整页白抓」，不额外花钱                      |

## 项目结构

```
app/
  graph/            # LangGraph 状态机：builder + 节点 + prompts + state
  models/           # LLM 工厂（模型分级）+ Pydantic 结构化 schema
  search/           # Tavily 客户端 + 来源可信度 + 轻量去重
  scraping/         # 网页正文抽取（trafilatura，带重试）
  analysis/         # 数据点分组/选优（纯函数）
  eval/             # LLM-as-a-Judge 评测
  storage/          # SQLite 历史 + Redis 缓存（防穿透/击穿/雪崩）
  service.py        # 统一入口：缓存编排 + 落盘 + 历史 + Langfuse
  mcp_server.py     # MCP 封装（FastMCP，stdio）
  api.py            # FastAPI HTTP 层（异步 job + 历史接口）
  static/index.html # 极简前端（原生 JS，无构建）
  logging_config.py # 日志走 stderr（MCP stdio 协议通道是 stdout）
scripts/
  eval_research.py  # 评测脚本
tests/              # 42 个离线单测（不调用大模型）
```

## 部署

Docker 一键部署（阿里云免费试用步骤见 [DEPLOY.md](DEPLOY.md)）：

```bash
docker compose up -d --build
```

密钥通过 `config/.env` 在运行时注入（`.env` 和 `.dockerignore` 双重保证不进镜像）；SQLite 数据挂载 `./data` 卷持久化。

## 技术栈

LangGraph · 阿里云百炼 Qwen（langchain-openai）· Pydantic v2 · Tavily · trafilatura · FastMCP · FastAPI · SQLite · Redis · Langfuse · Docker · ruff / pytest / uv

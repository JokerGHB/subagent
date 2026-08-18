# 多 Agent 自动化调研分析系统

输入一个调研主题 → 自动规划、联网搜索、过滤噪声、抽取数据、交叉验证 → 输出一份**带来源溯源、冲突标注**的结构化 Markdown 调研报告。

基于 **LangGraph** 构建的多 Agent 流水线，集成 **阿里云百炼 Qwen** 分级模型与 **Tavily** 搜索，可封装为标准 **MCP 工具** 接入任意 AI 客户端（Claude Desktop / Cursor 等）。

## 特性

- **多 Agent 分工**：规划 → 搜索 → 抽取 → 分析 四类 Agent 各司其职，互不干扰、可单独替换。
- **并行扇出**：子任务与来源两级并行（LangGraph `Send` API），一条调研流水线跑完约 1~3 分钟。
- **防幻觉设计**：每个数据点强制带原文摘录 + 来源 URL；多来源数值冲突时**标注分歧**而非强行合并。
- **强类型约束**：抽取与分析结果用 Pydantic 结构化输出，`value` 字段强制纯数字——类型约束比提示词更能保证格式。
- **LLM-as-a-Judge 评测**：用最强模型对产出按 4 项指标打分，量化系统质量。
- **成本可控**：搜索量、正文长度均可配置；结果落盘复用，评测不重跑调研。
- **MCP 封装**：整个调研系统暴露为 3 个标准 MCP 工具，可直接接入 Claude Desktop / Cursor。

## 系统架构

```
        调研主题
           │
           ▼
 ┌───────────────────────────────┐
 │  任务规划 Agent (planner)     │  拆分为 2~4 个可并行搜索的子任务
 │  qwen3.7-flash                │
 └───────────────────────────────┘
           │  Send 并行扇出（每子任务一分支）
           ▼
 ┌───────────────────────────────┐
 │  网页搜索 Agent (searcher)    │  每子任务搜 3 条来源 + 轻量去重
 │  Tavily 免费档                │
 └───────────────────────────────┘
           │
           ▼
         merge 汇聚点             │  跨子任务全局去重
           │  Send 并行扇出（每来源一分支）
           ▼
 ┌───────────────────────────────┐
 │  信息抽取 Agent (extractor)   │  抽取强类型事实（纯数字 + 单位 + 来源）
 │  qwen3.7-flash                │
 └───────────────────────────────┘
           │
           ▼
 ┌───────────────────────────────┐
 │  数据分析 Agent (analyzer)    │  交叉验证 + 冲突标注 + 找共识
 │  qwen3.7-plus                 │
 └───────────────────────────────┘
           │
           ▼
        结构化调研报告（关键数据点 + 溯源 + 冲突说明）
```

两级 `Send` 并行扇出（Map-Reduce-Map）：**子任务**并行搜索 → 汇聚去重 → **来源**并行抽取 → 汇聚分析。只有汇聚点节点写全局进度，避免并行分支对同一状态通道重复写入。

## Agent 角色与模型分级

| Agent | 模型 | 职责 |
|-------|------|------|
| 规划 planner | qwen3.7-flash | 把主题拆成 2~4 个子任务 |
| 搜索 searcher | Tavily API | 联网搜索 + 来源可信度打分 + 去重 |
| 抽取 extractor | qwen3.7-flash | 网页 → 结构化事实（value 纯数字 / unit / 原文摘录） |
| 分析 analyzer | qwen3.7-plus | 同维度合并、冲突标注、丢弃噪声，输出关键数据点 |
| 评测 judge | qwen3.7-max | 按相关性/可溯源性/覆盖度/冲突处理 4 项打分 |

按任务难度分级用模型（flash 干重活、plus/max 干关键判断），在保证质量的同时压低成本。

## 快速开始

### 1. 安装依赖

```bash
uv sync
```

### 2. 配置密钥

复制 `config/.env.example` 为 `config/.env`，填入：

```ini
DASHSCOPE_API_KEY=sk-xxx            # 阿里云百炼（https://bailian.console.aliyun.com）
TAVILY_API_KEY=tvly-xxx             # Tavily 搜索（https://app.tavily.com，免费 1000 次/月）
```

> `config/.env` 已被 `.gitignore` 忽略，请勿提交。

### 3. 跑一次调研

```bash
uv run python run.py "国产大模型市场规模"
```

结果打印为 Markdown，并落盘到 `data/last_result.json`。

### 4. 评测（复用上次结果，不重跑调研）

```bash
uv run python -m scripts.eval_research            # 复用上次结果
uv run python -m scripts.eval_research "新主题"   # 现场调研后评测
```

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

暴露的工具：

| 工具 | 说明 |
|------|------|
| `research_start(topic)` | 提交调研主题，返回 job_id（异步执行） |
| `research_get_status(job_id)` | 查询调研进度 |
| `research_get_result(job_id)` | 拉取调研结果（Markdown） |

## 评测结果（实测）

用 qwen-max 对一次真实调研（「国产大模型市场规模」）打分，平均 **4.0/5**：

| 指标 | 得分 | 说明 |
|------|------|------|
| 可溯源性 | 5/5 | 全部数据点带原文摘录 + 来源 URL |
| 冲突处理 | 5/5 | 同一来源内 106.3% vs 206.3% 的矛盾被标注并附数学验算 |
| 相关性 | 3/5 | 部分外围指标（智算云/投资事件/专利）偏离主题 |
| 覆盖度 | 3/5 | 缺竞争格局、应用渗透率等维度 |

评测体系有效：不是全满分，能真实暴露系统的短板。

## 成本控制

| 手段 | 效果 |
|------|------|
| `tavily_max_results = 3` | 来源数 ~20 → ~12，抽取调用降 ~40% |
| `extractor_max_chars = 1500` | 限制喂给抽取模型的正文长度 |
| snippet ≥100 字不抓正文 | 连 HTTP 请求都省 |
| 结果落盘复用 | 评测不再重跑 3 分钟调研 |
| 抓取失败免费重试（LLM 调用绝不重试） | 把「整页白抓」救回来，不额外花钱 |

## 项目结构

```
app/
  graph/            # LangGraph 状态机：builder + 4 个 Agent 节点 + prompts + state
  models/           # LLM 工厂（模型分级）+ Pydantic 结构化 schema
  search/           # Tavily 客户端 + 来源可信度 + 轻量去重
  scraping/         # 网页正文抽取（trafilatura，带重试）
  analysis/         # 数据点分组/选优（纯函数）
  eval/             # LLM-as-a-Judge 评测
  service.py        # 统一入口：调用 + Langfuse 观测 + 结果落盘
  mcp_server.py     # MCP 封装（FastMCP，stdio）
  logging_config.py # 日志走 stderr（MCP stdio 协议通道是 stdout，不能污染）
scripts/
  eval_research.py  # 评测脚本
tests/              # 16 个离线单测（不调用大模型）
```

## 技术栈

LangGraph · 阿里云百炼 Qwen（langchain-openai）· Pydantic v2 · Tavily · trafilatura · FastMCP · Langfuse · ruff / pytest / uv

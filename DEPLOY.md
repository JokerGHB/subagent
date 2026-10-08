# 部署指南（阿里云免费试用）

把多 Agent 调研系统部署成线上 HTTP 服务：浏览器打开即用，带 SQLite 历史 + Redis 缓存。

## 架构

```
浏览器 ──> FastAPI (app) ──> 多 Agent 调研图 ──> 百炼 LLM（Tavily 搜索）
                │   ▲
                ▼   │
           SQLite（历史）   Redis（缓存，命中秒回省 token）
```

## 前置条件

- 一台云服务器（阿里云免费试用即可），能装 Docker
- 服务器有公网 IP；`http://<IP>:8000` 直接访问（80/443 才需备案，非标准端口不用）
- 已有 `config/.env`（含 `DASHSCOPE_API_KEY`、`TAVILY_API_KEY`）——**不要提交到 git**

## 部署步骤

1. **装 Docker**（若服务器未装）：
   ```bash
   curl -fsSL https://get.docker.com | bash
   sudo systemctl enable --now docker
   ```

2. **拉取项目**（或上传代码）：
   ```bash
   git clone https://github.com/<你的用户名>/subagent.git
   cd subagent
   ```

3. **写密钥**：`config/.env` 内容与本地一致（DASHSCOPE_API_KEY / TAVILY_API_KEY /
   LANGFUSE 配置可选），另加管理员令牌 `ADMIN_TOKEN=<强密码>`（留空则管理员接口禁用）。
   `.env` 已在 .gitignore，不会被提交。

4. **一键起服务**（自动创建 app + redis 两个容器）：
   ```bash
   docker compose up -d --build
   ```

5. **验证**：
   ```bash
   curl http://localhost:8000/          # 返回前端页面
   curl http://localhost:8000/history/hot        # 热门排行（空数组也正常）
   curl http://localhost:8000/admin/history \
        -H "Authorization: Bearer <ADMIN_TOKEN>" # 管理员全量历史（token 错/缺 → 401/403）
   curl -X POST http://localhost:8000/research \
        -H "Content-Type: application/json" \
        -d '{"topic":"国产大模型市场分析"}'   # 返回 job_id，轮询拿报告
   ```

6. **放行安全组端口**：阿里云控制台 → 安全组 → 入方向放行 8000，然后浏览器访问
   `http://<公网IP>:8000`。

## 常用运维

| 操作 | 命令 |
|---|---|
| 看日志 | `docker compose logs -f app` |
| 重启 | `docker compose restart` |
| 只起 Redis（本地调试缓存） | `docker compose up -d redis` |
| 重新构建（改代码后） | `docker compose up -d --build` |
| 停止 | `docker compose down`（数据在 ./data 卷里不丢） |

## 构建卡住？先看这两条（国内服务器）

Dockerfile 已刻意避开所有国际链路：不用 `COPY --from=ghcr.io/astral-sh/uv`（拉 uv 镜像）、
不用 `curl https://astral.sh/uv/install.sh`（它的脚本最终还是去 GitHub releases 下二进制）、
也不 `apt-get` 装 curl。uv 改为从**清华 PyPI 源**装 wheel，`uv sync` 也走同一个源。

坑：uv **不认 `PIP_INDEX_URL`**（只设它，uv 仍旧去 pypi.org 拉，等于白配），
换源必须设 `UV_DEFAULT_INDEX`。

若仍然卡住，按顺序排查：

1. **服务器上的代码是不是最新的**：`git pull`。
   旧版 Dockerfile 里写的是 `COPY --from=ghcr.io/astral-sh/uv`，ghcr.io 在国内基本拉不动
   —— 现象是构建停在 `FROM ghcr.io/astral-sh/uv:latest`，几十秒才下十几 MB。
2. **基础镜像在不在本机**：`docker images | grep python`。
   没有 `python:3.12-slim` 就可能卡在 Docker Hub，需要给 Docker 配镜像加速器。
3. 看清卡在哪一层：构建日志里的 `[N/6]` 步骤号 + 那句命令，比「很慢」有用得多。

## 进阶（可选）

- **HTTPS**：装 Caddy 做反向代理，几行配置自动配证书：
  ```caddyfile
  your.domain.com {
      reverse_proxy localhost:8000
  }
  ```
  建议线上开 HTTPS：纯 `http://公网IP` 属于浏览器"不安全上下文"，部分安全 API
  （如 `crypto.randomUUID`）不可用。前端已做降级兜底，但 HTTPS 能一次解决所有这类问题。
- **缓存刷新**：前端勾选「强制刷新」或 `POST /research` 带 `"force": true`，
  跳过缓存重新调研并覆盖旧缓存。
- **性能说明**：FastAPI job 注册表在内存，进程重启丢「进行中的任务」，但历史都在 SQLite，不丢。

## 安全注意

- `ADMIN_TOKEN` 是管理员后门密钥：设强密码、勿外泄、勿提交 git（已在 .gitignore）
- 服务器上的 `config/.env` 权限：`chmod 600 config/.env`
- 若对外网开放，建议加 Caddy Basic Auth 或只对内网开放 8000

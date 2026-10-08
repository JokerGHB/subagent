# 单阶段 + 层缓存：依赖先装（改代码不重装依赖），源码后拷
FROM python:3.12-slim

# 装 uv：拿国内 PyPI 镜像上的官方 wheel。
# 不要 COPY --from=ghcr.io/astral-sh/uv（ghcr.io 拉不动）、也不要 curl astral.sh
# 的安装脚本（它最终从 GitHub releases 下二进制）—— 这两条都是国际链路，
# 国内服务器上会长时间卡住（本项目在腾讯云 2C2G 上两种写法都实测卡死）。
# 也不用 apt 装 curl（省一次 apt-get update，顺带省掉装完再卸的折腾）。
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple uv

WORKDIR /app

# 预编译字节码 + 复用宿主缓存（uv 的安装缓存）
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0

# uv 换源必须用 UV_DEFAULT_INDEX —— **uv 不认 PIP_INDEX_URL**：
# 只设 PIP_INDEX_URL 时 uv 仍旧去 pypi.org 拉（实测 verify），等于白配。
# 改成 UV_DEFAULT_INDEX 后 uv sync 才真的走清华源；锁文件里记录的 registry
# 是 pypi.org 也不影响，--frozen 照样通过（实测）。
ENV UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
# 慢链路下 uv 默认 30s 请求超时容易让整层失败重来，放宽一点
ENV UV_HTTP_TIMEOUT=120

# 先拷依赖清单并安装 → 后续改源码不会让这层缓存失效
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# 再拷源码（含 app/、config/、run.py）
COPY . .

# 密钥不进镜像：.env 由 docker-compose 在运行时注入；data/ 用 volume 挂载持久化
ENV REDIS_URL=redis://redis:6379/0

EXPOSE 8000
# .venv 由 uv sync 生成，uv run 自动用它
CMD ["uv", "run", "uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]

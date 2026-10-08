# 多阶段 + 层缓存：依赖先装（改代码不重装依赖），源码后拷
FROM python:3.12-slim

# 直接在slim镜像内安装uv，不再从ghcr镜像COPY，绕开ghcr.io
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && curl -LsSf https://astral.sh/uv/install.sh | sh \
    && mv /root/.cargo/bin/uv /root/.cargo/bin/uvx /bin/ \
    && apt-get purge -y curl \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# 预编译字节码 + 复用宿主缓存（uv 的安装缓存）
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
# 配置uv全局使用清华源
ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

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

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

# uv 的换源变量（**uv 不认 PIP_INDEX_URL**：只设它时 uv 仍旧请求 pypi.org，
# 实测 DEBUG 里明明白白写着 https://pypi.org/simple/…，等于白配）。
# 但注意：光设这个对下面这层**不管用** —— 真正起作用的是那行 sed，见下。
ENV UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
# 慢链路下 uv 默认 30s 请求超时容易让整层失败重来，放宽一点
ENV UV_HTTP_TIMEOUT=120

# 先拷依赖清单并安装 → 后续改源码不会让这层缓存失效
COPY pyproject.toml uv.lock ./
# 关键一步：uv.lock 里每个包都带着**绝对下载 URL + hash**
# （1456 处 files.pythonhosted.org）和 registry 地址（137 处 pypi.org/simple）。
# `uv sync --frozen` 是照单下载这些 URL —— 所以只设 UV_DEFAULT_INDEX 没用：
# 实测仍是 256 个请求打 CDN、0 个走镜像，国内服务器就卡在这（现象：构建日志里
# 几行 "Downloading lxml…" 几百秒一动不动，一个包都下不完）。
# 把 host 换成清华源即可：它镜像同一套 /packages/ 路径、文件字节一致，
# 所以 lock 里的 hash 照样校验通过（实测 256/256 全走清华源、依赖可导入）。
# 只改镜像里的这份副本，不动仓库里的 uv.lock。
RUN sed -i -e 's|https://files.pythonhosted.org|https://pypi.tuna.tsinghua.edu.cn|g' \
           -e 's|https://pypi.org/simple|https://pypi.tuna.tsinghua.edu.cn/simple|g' uv.lock \
 && uv sync --frozen --no-dev

# 再拷源码（含 app/、config/、run.py）
COPY . .

# 密钥不进镜像：.env 由 docker-compose 在运行时注入；data/ 用 volume 挂载持久化
ENV REDIS_URL=redis://redis:6379/0

EXPOSE 8000
# .venv 由 uv sync 生成，uv run 自动用它
CMD ["uv", "run", "uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]

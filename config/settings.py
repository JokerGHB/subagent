"""配置层：从 .env 读取所有配置项。

原理：pydantic-settings 按「字段名不区分大小写」自动把环境变量注入字段，
所以字段 dashscope_api_key 会自动匹配环境变量 DASHSCOPE_API_KEY。
"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录 = config/ 的上一级
BASE_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",  # .env 里多余的变量不报错
    )

    # ---- 大模型（百炼 OpenAI 兼容端点）----
    dashscope_api_key: str = ""
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    # 模型分级：按 Agent 角色选档。原则看两件事——「调用次数」和「串行还是并行」：
    # 并行扇出（extractor 每来源一次）是 token 大头；串行节点（planner/analyzer/writer）
    # 每一次的耗时都直接叠进用户等待时长。所以：重活/串行写作走 flash，关键判断走 max。
    llm_planner: str = "qwen3.8-flash"    # 拆 2~4 个子任务，简单结构化；串行 → 要快
    llm_extractor: str = "qwen3.8-flash"  # 每个来源调一次，调用次数最多 → token 大头
    llm_writer: str = "qwen3.8-flash"     # 输出 800~1000 字；实测从 max 的 ~104s 降到 ~40s
    llm_analyzer: str = "qwen3.8-max"     # 只调 1 次，但数字分析/冲突标注要稳 → 不降级
    llm_judge: str = "qwen3.8-max"        # 离线评测，调用少 → 用最强
    # 死配置：全仓库没有节点调用 get_searcher_llm（searcher 只走 Tavily API）。
    # 保留字段作为「将来给搜索做 LLM 打分/重排」的开关，但别以为它现在生效。
    llm_searcher: str = "qwen3.8-flash"
    # 单次 LLM 调用的超时（秒）。不设的话模型挂起时任务会永远 running —— 有了它才会
    # 快速失败并走到「失败显式上报」那条路（权限/参数错误本来就是快速失败，不受影响）。
    llm_timeout: int = 120

    # ---- 搜索 ----
    tavily_api_key: str = ""
    # 每个子任务最多返回的来源数 —— 它直接决定 extractor 的调用次数
    # （每个来源调一次抽取模型），是 token 成本的最大旋钮。
    tavily_max_results: int = 3

    # ---- 信息抽取 ----
    # 喂给抽取模型的正文长度上限：越长，输入 token 越多。截断够用即可。
    extractor_max_chars: int = 1500

    # ---- 观测 ----
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "http://localhost:3000"

    # ---- 缓存（Redis）----
    # 同主题调研缓存：命中直接秒回，省 token。Docker 里用 redis://redis:6379/0。
    redis_url: str = "redis://localhost:6379/0"
    cache_ttl_base: int = 86400      # 缓存基础 TTL（24h）
    cache_ttl_jitter: int = 1800     # TTL 抖动范围 ±30 分钟，防雪崩（各主题过期时刻错开）
    cache_empty_ttl: int = 300       # 空值缓存 TTL（5 分钟），防穿透（无结果主题不反复打 LLM）
    cache_lock_ttl: int = 300        # 重建互斥锁 TTL（5 分钟自动过期，防死锁），防击穿

    # ---- 管理员 ----
    # 管理员接口（GET /admin/history）的鉴权令牌；留空则管理员接口禁用（返回 403）。
    # 默认留空 —— 真实令牌写在 config/.env（已 gitignore），不要把口令提交进仓库。
    admin_token: str = ""


# 全局单例：任何模块 import settings 都拿到同一个实例
settings = Settings()
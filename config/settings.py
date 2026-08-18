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

    # 模型分级：按 Agent 角色选择不同档位的 Qwen
    llm_planner: str = "qwen3.7-flash"
    llm_searcher: str = "qwen3.7-flash"
    llm_extractor: str = "qwen3.7-flash"
    llm_analyzer: str = "qwen3.7-plus"
    llm_writer: str = "qwen3.7-max"
    llm_judge: str = "qwen3.7-max"

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


# 全局单例：任何模块 import settings 都拿到同一个实例
settings = Settings()
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
    llm_planner: str = "qwen-plus"
    llm_searcher: str = "qwen-flash"
    llm_extractor: str = "qwen-flash"
    llm_analyzer: str = "qwen-plus"
    llm_writer: str = "qwen-max"
    llm_judge: str = "qwen-max"

    # ---- 搜索 ----
    tavily_api_key: str = ""
    tavily_max_results: int = 5

    # ---- 观测 ----
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "http://localhost:3000"


# 全局单例：任何模块 import settings 都拿到同一个实例
settings = Settings()
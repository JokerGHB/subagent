"""模型接入层：统一走百炼的 OpenAI 兼容端点，按 Agent 角色分级。

学习要点：
- 百炼提供 OpenAI 兼容模式，langchain-openai 的 ChatOpenAI 直接可用，
  只需把 base_url 指向兼容端点、传入 API Key。
- 以后想换模型厂商（如 Claude），只需改 base_url + model，业务代码不动。
"""
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from config.settings import settings


def _chat(model: str, temperature: float = 0.2) -> ChatOpenAI:
    """构造一个指向百炼兼容端点的 ChatOpenAI 客户端。"""
    return ChatOpenAI(
        model=model,
        # 新版 langchain 把 api_key 类型标成 SecretStr；pydantic 的 SecretStr 正是为密钥设计
        api_key=SecretStr(settings.dashscope_api_key),
        base_url=settings.dashscope_base_url,
        temperature=temperature,
    )


def get_planner_llm() -> ChatOpenAI:
    """规划 Agent：中等推理。"""
    return _chat(settings.llm_planner)


def get_searcher_llm() -> ChatOpenAI:
    """搜索 Agent：高频调用，用最便宜的 flash。"""
    return _chat(settings.llm_searcher)


def get_extractor_llm() -> ChatOpenAI:
    """抽取 Agent：高频 + 结构化输出，用 flash。"""
    return _chat(settings.llm_extractor)


def get_analyzer_llm() -> ChatOpenAI:
    """分析 Agent：数字分析要稳，用 plus。"""
    return _chat(settings.llm_analyzer)


def get_writer_llm() -> ChatOpenAI:
    """报告 Agent：质量影响 Demo，用 max；报告可以更有创造性。"""
    return _chat(settings.llm_writer, temperature=0.7)


def get_judge_llm() -> ChatOpenAI:
    """评测打分 Agent：用最强模型。"""
    return _chat(settings.llm_judge)

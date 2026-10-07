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
    """构造一个指向百炼兼容端点的 ChatOpenAI 客户端。

    timeout 必须显式设：默认不超时，模型端挂起时任务会永远 running、进度条也永远
    卡在某个阶段。设了超时才会抛错 → 走到「失败显式上报」那条路。
    但超时值必须**大于最慢节点的 p99**，否则就是把「慢」人为变成「失败」：
    analyzer 吐 ~2000 字符在开思考时要 144s，120s 会把它打死（本项目真实踩过）。

    enable_thinking：百炼 qwen3.x 默认开显式思考，实测同任务慢 8 倍
    （max：13.9 字符/s → 114 字符/s）。关掉它，模型档位不变、速度回到秒级。

    max_retries=1：超时是不可"重试掉"的错误——重试一次就是再等一个完整的超时
    （曾用 2 次重试把 120s 的等待放大成 362s 才报错）。留 1 次给限流/连接抖动这类
    快速失败的错误，它们重试很便宜。
    """
    return ChatOpenAI(
        model=model,
        # 新版 langchain 把 api_key 类型标成 SecretStr；pydantic 的 SecretStr 正是为密钥设计
        api_key=SecretStr(settings.dashscope_api_key),
        base_url=settings.dashscope_base_url,
        temperature=temperature,
        timeout=(
            settings.llm_timeout_thinking
            if settings.llm_enable_thinking
            else settings.llm_timeout
        ),
        max_retries=1,
        extra_body={"enable_thinking": settings.llm_enable_thinking},
    )


def get_planner_llm() -> ChatOpenAI:
    """规划 Agent：拆子任务，简单结构化输出，用 flash（串行节点，快即省等待）。"""
    return _chat(settings.llm_planner)


def get_searcher_llm() -> ChatOpenAI:
    """搜索 Agent：**当前未接线** —— searcher 只走 Tavily API，没人调用这个函数。

    保留是为了将来给搜索做 LLM 打分/重排时有个现成的入口。
    """
    return _chat(settings.llm_searcher)


def get_extractor_llm() -> ChatOpenAI:
    """抽取 Agent：每个来源调一次，调用次数最多，用 flash。"""
    return _chat(settings.llm_extractor)


def get_analyzer_llm() -> ChatOpenAI:
    """分析 Agent：只调 1 次但要稳（数字分析、冲突标注），用 max。"""
    return _chat(settings.llm_analyzer)


def get_writer_llm() -> ChatOpenAI:
    """报告 Agent：输出 800~1000 字，用 flash；报告需要文字组织，temperature 高一些。"""
    return _chat(settings.llm_writer, temperature=0.7)


def get_judge_llm() -> ChatOpenAI:
    """评测打分 Agent：用最强模型（离线评测，调用少）。"""
    return _chat(settings.llm_judge)

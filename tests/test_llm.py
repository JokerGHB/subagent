"""LLM 工厂配置测试（离线，零 LLM 调用）：把「模型分级 / 关思考 / 超时 / 重试」钉死。

为什么值得测：这几个值是纯配置，改错了不会有任何报错，只会体现为
「线上很慢」或「任务全失败」——正是本项目踩过的两个坑（见 CLAUDE.md）：
- 超时设得比最慢节点的 p99 还短 → 把「慢」人为变成「失败」；
- 开着显式思考 → 实测慢 8 倍（13.9 字符/s vs 114 字符/s）。
"""
from typing import ClassVar

import pytest

from app.models import llm
from config.settings import settings


class RecordingChat:
    """替身：只记录构造参数，不发任何请求。"""

    last_kwargs: ClassVar[dict] = {}

    def __init__(self, **kwargs):
        RecordingChat.last_kwargs = kwargs


@pytest.fixture(autouse=True)
def patch_chat(monkeypatch):
    """替换 ChatOpenAI 并保证每个用例看到的是干净的 kwargs。"""
    RecordingChat.last_kwargs = {}
    monkeypatch.setattr(llm, "ChatOpenAI", RecordingChat)


def test_thinking_disabled_by_default():
    """默认关思考：这是把 analyzer 从 144s 超时拉回 30s 的关键开关。"""
    assert settings.llm_enable_thinking is False
    llm.get_analyzer_llm()
    assert RecordingChat.last_kwargs["extra_body"] == {"enable_thinking": False}


def test_thinking_on_switches_to_long_timeout(monkeypatch):
    """开思考时必须自动换成长超时，否则「慢」又会被当成「失败」。"""
    monkeypatch.setattr(settings, "llm_enable_thinking", True)
    llm.get_analyzer_llm()
    kwargs = RecordingChat.last_kwargs
    assert kwargs["extra_body"] == {"enable_thinking": True}
    assert kwargs["timeout"] == settings.llm_timeout_thinking
    assert kwargs["timeout"] > settings.llm_timeout


def test_short_timeout_when_thinking_off():
    llm.get_analyzer_llm()
    assert RecordingChat.last_kwargs["timeout"] == settings.llm_timeout


def test_retries_capped_at_one():
    """超时不可"重试掉"：2 次重试曾把 120s 的等待放大成 362s 才报错。"""
    llm.get_analyzer_llm()
    assert RecordingChat.last_kwargs["max_retries"] == 1


def test_each_role_uses_its_configured_model():
    """六个角色各取自己的模型配置（模型分级就活在这一层）。"""
    cases = [
        (llm.get_planner_llm, settings.llm_planner),
        (llm.get_searcher_llm, settings.llm_searcher),
        (llm.get_extractor_llm, settings.llm_extractor),
        (llm.get_analyzer_llm, settings.llm_analyzer),
        (llm.get_writer_llm, settings.llm_writer),
        (llm.get_judge_llm, settings.llm_judge),
    ]
    for getter, expected in cases:
        RecordingChat.last_kwargs = {}  # 清干净，确保读到的是本次调用的 kwargs
        getter()
        assert RecordingChat.last_kwargs["model"] == expected, getter.__name__


def test_model_tiering_matches_documented_division():
    """分级本身也是契约：高频/串行节点 flash，关键判断 max。"""
    assert settings.llm_extractor == settings.llm_writer == settings.llm_planner
    assert "flash" in settings.llm_planner
    assert settings.llm_analyzer == settings.llm_judge
    assert "max" in settings.llm_analyzer


def test_writer_uses_higher_temperature():
    """写报告要文字组织，temperature 高于其它节点（0.7 vs 0.2）。"""
    llm.get_writer_llm()
    assert RecordingChat.last_kwargs["temperature"] == 0.7
    llm.get_analyzer_llm()
    assert RecordingChat.last_kwargs["temperature"] == 0.2


def test_base_url_and_key_are_injected(monkeypatch):
    """端点与密钥从 settings 注入（换厂商只改 base_url，业务代码不动）。"""
    monkeypatch.setattr(settings, "dashscope_base_url", "https://example.invalid/v1")
    llm.get_planner_llm()
    kwargs = RecordingChat.last_kwargs
    assert kwargs["base_url"] == "https://example.invalid/v1"
    assert kwargs["api_key"].get_secret_value() == settings.dashscope_api_key

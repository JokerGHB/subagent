"""MCP server 冒烟测试（进程内 Client，不发真实调研任务）。

用 fastmcp.Client 直接连 FastMCP 实例，验证：工具注册、网页搜索、错误处理、
结果渲染。完整调研链路（research_start 在后台跑全图）成本高，单独手动验证。
"""
import asyncio
import json

from fastmcp import Client

from app.mcp_server import _render_keypoints_markdown, mcp
from app.models.schemas import KeyPoint


def _run(coro):
    return asyncio.run(coro)


def test_tools_registered():
    """四个工具都应注册成功。"""
    async def check():
        async with Client(mcp) as client:
            tools = await client.list_tools()
            names = {t.name for t in tools}
            assert {
                "research_start",
                "research_get_status",
                "research_get_result",
                "research_search_web",
            } <= names

    _run(check())


def test_search_web_returns_markdown():
    """直接搜索返回 Markdown 结果（走真实 Tavily，快速）。"""
    async def check():
        async with Client(mcp) as client:
            result = await client.call_tool(
                "research_search_web",
                {"query": "中国AI市场规模 2025", "max_results": 3},
            )
            text = result.content[0].text
            assert "搜索结果" in text
            assert "URL:" in text

    _run(check())


def test_job_not_found():
    """不存在的 job_id 应返回明确的错误/未找到。"""
    async def check():
        async with Client(mcp) as client:
            r = await client.call_tool(
                "research_get_status", {"job_id": "deadbeef0000"}
            )
            assert json.loads(r.content[0].text)["status"] == "not_found"

            r2 = await client.call_tool(
                "research_get_result", {"job_id": "deadbeef0000"}
            )
            assert "不存在" in r2.content[0].text

    _run(check())


def test_render_keypoints_markdown():
    """关键数据点渲染成 Markdown 报告。"""
    kp = KeyPoint(
        dimension="市场规模",
        value=294.16,
        unit="亿元",
        time="2024年",
        source_count=2,
        sources=["https://example.com/a"],
        quote="市场规模达294亿元",
        conflict="",
    )
    md = _render_keypoints_markdown({"topic": "测试主题", "key_points": [kp]})
    assert "# 调研报告：测试主题" in md
    assert "市场规模: 294.16亿元 (2024年)" in md
    assert "印证来源数：2" in md

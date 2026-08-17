"""MCP 完整链路验证脚本：发起调研 → 轮询状态 → 取结果。

演示 MCP 客户端侧调用姿势：一次 research_start 拿 job_id，循环
research_get_status 轮询，done 后 research_get_result 取 Markdown 报告。

运行: uv run python scripts/e2e_mcp_check.py "主题"（默认国产大模型市场规模）
"""
import asyncio
import json
import sys
import time

from fastmcp import Client

from app.logging_config import setup_logging
from app.mcp_server import mcp

TIMEOUT = 360  # 秒


async def main(topic: str) -> None:
    async with Client(mcp) as client:
        start = await client.call_tool("research_start", {"topic": topic})
        job = json.loads(start.content[0].text)
        print(f"START: job_id={job['job_id']} status={job['status']}")

        deadline = time.time() + TIMEOUT
        final = None
        while time.time() < deadline:
            st = json.loads(
                (await client.call_tool(
                    "research_get_status", {"job_id": job["job_id"]}
                )).content[0].text
            )
            final = st
            if st["status"] in ("done", "error"):
                break
            await asyncio.sleep(3)

        print("FINAL:", json.dumps(
            {k: final.get(k) for k in
             ("status", "subtasks", "sources", "facts", "key_points", "error")
             if k in final},
            ensure_ascii=False,
        ))

        if final.get("status") == "done":
            result = await client.call_tool(
                "research_get_result", {"job_id": job["job_id"]}
            )
            print("\n===== 调研报告 =====")
            print(result.content[0].text)
        else:
            print("任务未在限时内完成，或出错，见上方状态。")


if __name__ == "__main__":
    setup_logging()
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "国产大模型市场规模"))

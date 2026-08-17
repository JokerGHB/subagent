"""规划 Agent：用模型把调研主题拆成可并行搜索的子任务。

学习点：
- 让模型输出结构化结果（JSON 数组）而不是自由文本，后续才好程序化处理。
- 模型偶尔输出不规范的 JSON（带 ```json 围栏、解释文字），所以要 _extract_json_array
  做健壮解析；解析失败要兜底，不能直接崩掉整个流程。
"""
import json
import re

from app.graph.prompts import PLANNER_PROMPT
from app.graph.state import ResearchState, Subtask
from app.models.llm import get_planner_llm


def _extract_json_array(text: str) -> list:
    """从模型回复里稳健地抠出 JSON 数组。"""
    # 去掉 ```json ``` 代码块围栏
    text = re.sub(r"```(?:json)?", "", text).strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        raise ValueError(f"模型输出中没有 JSON 数组: {text[:200]}")
    return json.loads(text[start : end + 1])


def planner_node(state: ResearchState) -> dict:
    topic = state["topic"]
    llm = get_planner_llm()
    response = llm.invoke(PLANNER_PROMPT.format(topic=topic))
    raw = response.content
    print(f"[planner] 模型返回: {str(raw)[:120]}...")

    try:
        subtasks: list[Subtask] = _extract_json_array(raw)
        if not subtasks:
            raise ValueError("空数组")
    except Exception as e:
        # 兜底：拆不动就整主题当成一个子任务，保证流程不断
        print(f"[planner] JSON 解析失败，回退为单个子任务: {e}")
        subtasks = [{"id": "1", "topic": topic, "intent": "综合调研"}]

    print(f"[planner] 拆出 {len(subtasks)} 个子任务")
    return {"subtasks": subtasks, "status": "planned"}

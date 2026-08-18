"""service 落盘逻辑测试：验证 last_result.json + report.md 双落盘。"""
from pathlib import Path

from app import service


def test_save_result_writes_json_and_report_md(monkeypatch, tmp_path: Path):
    # 把模块级路径常量指向临时目录，避免污染真实 data/
    # （RESULT_FILE/REPORT_FILE 是 import 时按 DATA_DIR 算好的，所以要分别 patch）
    monkeypatch.setattr(service, "RESULT_FILE", tmp_path / "last_result.json")
    monkeypatch.setattr(service, "REPORT_FILE", tmp_path / "report.md")
    result = {
        "topic": "测试主题",
        "status": "written",
        "subtasks": [],
        "sources": [],
        "facts": [],
        "key_points": [],
        "report": "# 测试报告\n\n结论：通过",
    }
    service.save_result(result)

    assert (tmp_path / "last_result.json").exists()
    md = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert md == "# 测试报告\n\n结论：通过"

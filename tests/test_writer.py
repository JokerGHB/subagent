"""报告渲染纯函数测试：render_report_markdown 不调用任何模型，离线可跑。"""
from app.graph.nodes.writer import render_report_markdown
from app.models.schemas import Report, ReportSection


def _sample_report() -> Report:
    return Report(
        title="国产大模型市场调研报告",
        overview="本报告基于多来源数据交叉验证得出。",
        sections=[
            ReportSection(
                title="市场规模",
                body="2024 年中国大模型市场规模达 294.16 亿元。",
                sources=["https://a.com", "https://b.com"],
            ),
            ReportSection(
                title="市场增速",
                body="2020-2024 年复合增长率约 106.3%。",
                sources=["https://a.com"],
            ),
        ],
        conclusion="市场处于高速增长期，竞争格局尚不稳固。",
    )


def test_render_full_report():
    md = render_report_markdown(_sample_report())
    assert md.startswith("# 国产大模型市场调研报告")
    assert "## 关键发现" in md
    assert "### 市场规模" in md
    assert "### 市场增速" in md
    assert "**来源：**" in md
    assert "## 结论" in md
    assert "市场处于高速增长期" in md


def test_render_sources_inside_sections():
    md = render_report_markdown(_sample_report())
    # 每个小节正文下方跟着自己的来源
    assert "**来源：** [https://a.com](https://a.com) · [https://b.com](https://b.com)" in md


def test_render_dedup_in_appendix():
    md = render_report_markdown(_sample_report())
    appendix = md.split("## 来源附录")[1]
    # 按"附录行"统计：a.com 被两个小节引用，但附录里只应有一行
    # （注意不能按子串 count，链接 [url](url) 里同一 url 会出现两次）
    lines = [ln for ln in appendix.splitlines() if ln.strip().startswith("- [")]
    assert sum("https://a.com" in ln for ln in lines) == 1
    assert sum("https://b.com" in ln for ln in lines) == 1


def test_render_no_sections():
    report = Report(title="T", overview="O", conclusion="C")
    md = render_report_markdown(report)
    assert "## 关键发现" not in md
    assert "## 结论" in md
    assert "## 来源附录" not in md

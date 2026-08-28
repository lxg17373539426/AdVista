from pathlib import Path


STATIC = Path(__file__).resolve().parents[1] / "ad_vista_agent" / "web" / "static"


def test_web_tasks_have_clear_single_purpose_boundaries() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")

    assert "广告洞察" not in html
    assert "完整报告" not in html
    assert "查看结果" not in html
    assert "卖点分析报告" in html
    assert "证据提取" in html
    assert 'type="radio"' in html


def test_generated_downloads_are_rendered_in_conversation() -> None:
    script = (STATIC / "app.js").read_text(encoding="utf-8")

    assert "resultActions" in script
    assert "下载卖点分析报告（HTML）" in script
    assert "result-action" in script

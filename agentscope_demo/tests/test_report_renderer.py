from html.parser import HTMLParser

import pytest

from tools.report_renderer import render_file, render_report


class Document(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def test_report_tables_and_navigation():
    html = render_report("""# 示例企业财报

## **盈利能力**

| 指标 | 2025 |
|---|---:|
| 毛利率 | 30% |

## 盈利能力

> 风险提示：口径待确认。
""")
    tags = Document(html).tags
    assert ("h2", {"id": "section-1"}) in tags
    assert ("h2", {"id": "section-2"}) in tags
    assert ("a", {"href": "#section-1"}) in tags
    assert "<table>" in html and "<td>毛利率</td>" in html
    assert "30%" in html and "<blockquote>" in html
    assert "**盈利能力**" not in html
    assert not any(tag in {"script", "link"} for tag, _ in tags)


def test_untrusted_html_links_and_metadata_are_not_executable():
    html = render_report(
        '# 测试\n\n<script>alert(1)</script>\n\n[链接](javascript:alert(1))',
        metadata={"企业ID": '<img src=x onerror="alert(1)">'},
    )
    tags = Document(html).tags
    assert not any(tag in {"script", "img"} for tag, _ in tags)
    assert not any(attrs.get("href", "").startswith("javascript:") for _, attrs in tags)
    assert "&lt;script&gt;" in html
    assert "&lt;img" in html


def test_convert_file_preserves_source_and_handles_dollar_signs(tmp_path):
    source = tmp_path / "报告.md"
    text = "# 财报\n\n金额 $100\n\n```text\nSY02 < 100\n```"
    source.write_text(text)
    output = render_file(source)
    assert output == source.with_suffix(".html")
    assert source.read_text() == text
    assert "$100" in output.read_text()
    assert "SY02 &lt; 100" in output.read_text()
    with pytest.raises(ValueError):
        render_file(source, source)

"""将财报 Markdown 转为无需外部资源的静态 HTML。"""
from html import escape
from pathlib import Path
from string import Template

from markdown_it import MarkdownIt


TEMPLATES = Path(__file__).resolve().parents[1] / "templates"


def render_report(markdown_text: str, *, metadata: dict | None = None) -> str:
    """保留报告正文，增加目录、响应式样式和打印样式。原始HTML按文本显示。"""
    parser = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])
    tokens = parser.parse(markdown_text)
    title = "财务分析报告"
    contents = []
    heading_index = 0
    title_found = False
    for index, token in enumerate(tokens):
        if token.type != "heading_open":
            continue
        label = tokens[index + 1].content
        # 目录和浏览器标题使用纯文本，避免出现 Markdown 强调符号。
        children = tokens[index + 1].children or []
        label = "".join(child.content for child in children if child.type in {"text", "code_inline"}) or label
        if token.tag == "h1" and not title_found:
            title = label
            title_found = True
        if token.tag == "h2":
            heading_index += 1
            anchor = f"section-{heading_index}"
            token.attrSet("id", anchor)
            contents.append(f'<a href="#{anchor}"><span>{heading_index:02d}</span>{escape(label)}</a>')

    def table_open(*_):
        return '<div class="table-scroll" tabindex="0" role="region" aria-label="财务数据表格，可横向滚动"><table>\n'

    parser.renderer.rules["table_open"] = table_open
    parser.renderer.rules["table_close"] = lambda *_: "</table></div>\n"
    body = parser.renderer.render(tokens, parser.options, {})
    details = "".join(
        f'<div class="meta-item"><dt>{escape(str(key))}</dt><dd>{escape(str(value))}</dd></div>'
        for key, value in (metadata or {}).items() if value is not None and value != ""
    )
    navigation = (
        '<aside class="sidebar" aria-label="报告目录"><div class="toc-label">报告目录</div>'
        f'<nav>{"".join(contents)}</nav><a class="back-top" href="#top">返回顶部 ↑</a></aside>'
        if contents else ""
    )
    template = Template((TEMPLATES / "report.html").read_text(encoding="utf-8"))
    return template.substitute(
        title=escape(title),
        css=(TEMPLATES / "report.css").read_text(encoding="utf-8"),
        metadata=f'<dl class="metadata">{details}</dl>' if details else "",
        navigation=navigation,
        body=body,
        layout_class="report-layout" if contents else "report-layout without-toc",
    )


def render_file(markdown_path: Path, output_path: Path | None = None) -> Path:
    """独立转换现有报告；默认输出到原文件旁的同名 .html。"""
    markdown_path = Path(markdown_path)
    output_path = Path(output_path) if output_path is not None else markdown_path.with_suffix(".html")
    if markdown_path.resolve() == output_path.resolve():
        raise ValueError("HTML输出路径不能与Markdown源文件相同")
    rendered = render_report(markdown_path.read_text(encoding="utf-8"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8")
    return output_path

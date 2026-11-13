"""将 Markdown 报告转为无需外部资源的静态 HTML，支持双报告 Tab 切换。"""
import re
from html import escape
from pathlib import Path
from string import Template

from markdown_it import MarkdownIt


TEMPLATES = Path(__file__).resolve().parents[1] / "templates"


def _mark_risk_warnings(tokens):
    """用渲染器生成警示样式，模型正文无需携带 HTML 标签。"""
    for index, token in enumerate(tokens):
        if token.type != "blockquote_open":
            continue
        depth, text = 1, ""
        for child in tokens[index + 1:]:
            if child.type == "blockquote_open":
                depth += 1
            elif child.type == "blockquote_close":
                depth -= 1
                if not depth:
                    break
            elif child.type == "inline":
                text += child.content
        if "一票否决" in text and re.search(r"(?:R[1-6].{0,8}已触发|一票否决触发|一票否决.{0,8}R[1-6])", text):
            token.attrSet("class", "risk-warning")



def render_report(markdown_text: str, *, metadata: dict | None = None) -> str:
    """保留报告正文，增加目录、响应式样式和打印样式。原始HTML按文本显示。"""
    parser = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])
    tokens = parser.parse(markdown_text)
    _mark_risk_warnings(tokens)
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

    parser.add_render_rule("table_open", table_open)
    parser.add_render_rule("table_close", lambda *_: "</table></div>\n")
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


def _md_to_html(markdown_text: str) -> tuple[str, list[str]]:
    """将 Markdown 转为 HTML，返回 (body_html, h2_headings)。"""
    parser = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])
    tokens = parser.parse(markdown_text)
    _mark_risk_warnings(tokens)
    headings = []
    heading_index = 0
    for index, token in enumerate(tokens):
        if token.type != "heading_open":
            continue
        children = tokens[index + 1].children or []
        label = "".join(child.content for child in children if child.type in {"text", "code_inline"}) or tokens[index + 1].content
        if token.tag == "h2":
            heading_index += 1
            anchor = f"section-{heading_index}"
            token.attrSet("id", anchor)
            headings.append(f'<a href="#{anchor}"><span>{heading_index:02d}</span>{escape(label)}</a>')

    def table_open(*_):
        return '<div class="table-scroll" tabindex="0" role="region" aria-label="表格，可横向滚动"><table>\n'
    parser.add_render_rule("table_open", table_open)
    parser.add_render_rule("table_close", lambda *_: "</table></div>\n")
    body = parser.renderer.render(tokens, parser.options, {})
    return body, headings


def render_combined_html(*, financial_report: str, related_party_report: str,
                         company_name: str, report_period: str,
                         scenario: str, generated_at: str) -> str:
    """将财报报告和关联交易报告合并为一个 HTML，通过 Tab 切换。"""
    fin_body, fin_headings = _md_to_html(financial_report)
    rp_body, rp_headings = _md_to_html(related_party_report)

    css = (TEMPLATES / "report.css").read_text(encoding="utf-8")

    tab_css = """
.tab-bar{display:flex;gap:0;border-bottom:2px solid #1a73e8;margin-bottom:1.5rem}
.tab-btn{flex:1;padding:.75rem 1rem;border:1px solid #ddd;border-bottom:none;background:#f5f5f5;cursor:pointer;font-size:.95rem;font-weight:600;color:#555;text-align:center;transition:all .15s}
.tab-btn:first-child{border-radius:6px 0 0 0}
.tab-btn:last-child{border-radius:0 6px 0 0}
.tab-btn.active{background:#1a73e8;color:#fff;border-color:#1a73e8}
.tab-content{display:none}
.tab-content.active{display:block}
"""

    combined_css = css + tab_css
    navigation = ""
    if fin_headings or rp_headings:
        nav_items = ""
        nav_items += '<optgroup label="财报分析">' + "".join(fin_headings) + "</optgroup>"
        nav_items += '<optgroup label="关联交易">' + "".join(rp_headings) + "</optgroup>"
        navigation = f'<aside class="sidebar" aria-label="报告目录"><div class="toc-label">报告目录</div><nav>{nav_items}</nav><a class="back-top" href="#top">返回顶部 ↑</a></aside>'

    title = escape(f"{company_name} 信贷分析报告")
    metadata = f"""<dl class="metadata">
<div class="meta-item"><dt>企业名称</dt><dd>{escape(company_name)}</dd></div>
<div class="meta-item"><dt>报告期</dt><dd>{escape(report_period)}</dd></div>
<div class="meta-item"><dt>分析场景</dt><dd>{escape(scenario)}</dd></div>
<div class="meta-item"><dt>生成时间</dt><dd>{escape(generated_at)}</dd></div>
</dl>"""

    # 构建 Tab 内容
    tab_content = f"""
<div class="tab-bar" role="tablist">
  <button class="tab-btn active" role="tab" aria-selected="true" data-tab="financial" onclick="switchTab('financial')">财报分析</button>
  <button class="tab-btn" role="tab" aria-selected="false" data-tab="related-party" onclick="switchTab('related-party')">关联交易风险排查</button>
</div>
<div id="tab-financial" class="tab-content active"><article class="prose">{fin_body}</article></div>
<div id="tab-related-party" class="tab-content"><article class="prose">{rp_body}</article></div>
<script>
function switchTab(name) {{
  document.querySelectorAll('.tab-content').forEach(e => e.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b => {{
    b.classList.remove('active');
    b.setAttribute('aria-selected', 'false');
  }});
  document.getElementById('tab-' + name).classList.add('active');
  document.querySelector('[data-tab="' + name + '"]').classList.add('active');
  document.querySelector('[data-tab="' + name + '"]').setAttribute('aria-selected', 'true');
}}
</script>
"""

    template = Template((TEMPLATES / "report.html").read_text(encoding="utf-8"))
    return template.substitute(
        title=title,
        css=combined_css,
        metadata=metadata,
        navigation=navigation,
        body=tab_content,
        layout_class="report-layout",
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


def render_investigation_html(*, reports, company_name, report_period, scenario, generated_at):
    """任意注册节点的独立报告 Tab；业务 Markdown 仍禁用原始 HTML。"""
    buttons, panels = [], []
    for index, (key, (label, report)) in enumerate(reports.items()):
        body, _ = _md_to_html(report)
        buttons.append(f'<button type="button" role="tab" aria-selected="{str(index == 0).lower()}" data-panel="report-{escape(key)}">{escape(label)}</button>')
        panels.append(f'<section id="report-{escape(key)}" class="skill-report prose" {"hidden" if index else ""}>{body}</section>')
    css = (TEMPLATES / "report.css").read_text(encoding="utf-8") + "\n.skill-tabs{display:flex;flex-wrap:wrap;gap:8px;margin:20px 0}.skill-tabs button{padding:10px;border:1px solid #cbdad6;background:#fff;border-radius:6px;cursor:pointer}.skill-tabs button[aria-selected=true]{background:#167967;color:white}.skill-report[hidden]{display:none}"
    script = """<script>document.querySelectorAll('[data-panel]').forEach(button=>button.addEventListener('click',()=>{document.querySelectorAll('[data-panel]').forEach(b=>b.setAttribute('aria-selected',String(b===button)));document.querySelectorAll('.skill-report').forEach(panel=>panel.hidden=panel.id!==button.dataset.panel)}));</script>"""
    return Template((TEMPLATES / "report.html").read_text(encoding="utf-8")).substitute(
        title=escape(company_name + " 全流程调查报告"), css=css,
        metadata=f'<p>{escape(report_period)}年度 · {escape(scenario)} · {escape(generated_at)}</p>',
        navigation="", body='<div class="skill-tabs" role="tablist">' + "".join(buttons) + '</div>' + "".join(panels) + script,
        layout_class="report-layout without-toc",
    )

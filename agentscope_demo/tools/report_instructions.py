"""区分业务分析资料与客户交付模板，移除会诱导报告输出内部审计/代码的示例。"""
import re


def remove_internal_sections(text):
    """只删除明确的审计、教学示例和维护章节，不删除业务分析/风险章节。"""
    text = re.sub(r"\A---\s*\n.*?\n---\s*\n", "", text, flags=re.S)
    lines, result, skipping = text.splitlines(), [], None
    for line in lines:
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            level, title = len(heading[1]), heading[2].strip()
            if skipping is not None and level <= skipping:
                skipping = None
            if re.match(r"(?:(?:[一二三四五六七八九十]+|\d+)[、.．]\s*)?(?:审计日志|审计追踪|示例|踩坑记录|当前 Demo 接入与编排)", title):
                skipping = level
        if skipping is None:
            result.append(line)
    return "\n".join(result).strip()


def analysis_instructions(text):
    # 先处理代码块，防止示例内的Markdown标题破坏外层章节边界。
    # 最终页面不执行 Mermaid 或 ASCII 代码图；关系证据改用表格呈现。
    text = re.sub(r"(?m)^ {0,3}```[^\n]*\n.*?^ {0,3}```[ \t]*$", "（代码示例省略；结构或股权关系使用纯Markdown表格表达）", text, flags=re.S | re.I)
    text = re.sub(r"```(?:json|mermaid|markdown)?", "代码围栏", text, flags=re.I)
    text = remove_internal_sections(text)
    text = text.replace("Mermaid", "Markdown关系表").replace("mermaid", "Markdown关系表")
    text = text.replace("红色标注", "Markdown引用加粗警示（颜色由页面负责）")
    return text


def delivery_template(text):
    text = analysis_instructions(text)
    # 模板版本等编写说明不属于交付正文；保留实际报告标题以后的部分。
    title = re.search(r"(?m)^#{1,2}\s+[^\n]*(?:报告|分析)[^\n]*(?:[—－-]|\[客户|\[企业)[^\n]*$", text)
    if title:
        text = text[title.start():]
    return text

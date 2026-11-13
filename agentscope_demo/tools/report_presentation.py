"""交付正文清理与检查：保留业务事实，只去除对话外壳和展示标签。"""
import re

PRESENTATION_RULES = """
【交付正文要求】
- 最终只返回报告，从报告标题开始；不写问候、致歉、修改说明、对用户的回复或“如需我可以”等结尾。
- 使用纯 Markdown，不输出 HTML/CSS/JavaScript 标签、代码围栏、模板使用说明。
- 风险警示使用 Markdown 引用和加粗，不能输出带 style 的 div；保留警示事实和处置建议。
- 客户、股东及关联企业名称逐字使用工具提供的企业全称，不写XX公司、某公司、占位符或自行缩写。
- 数据源仅提供简称/脱敏名称时，明确“企业全称未提供”，不得猜测补全真实名称。
- 保留模拟数据、未审计、征信缺失等业务限制，不把内部修正过程写进报告。
"""

CHATTER = r"您指出的问题|上一份报告|我已(?:重新|修改|修正)|以下是(?:修改|修正|重新生成|完整报告)|如(?:您)?需要[，, ]*(?:我|进一步)|希望以上|感谢您的"
TAG = r"</?[A-Za-z][^>]*>"


def normalize_presentation(report: str) -> tuple[str, bool]:
    original = report
    text = report.strip()
    fenced = re.fullmatch(r"```(?:markdown|md)?\s*\n(.*?)\n```", text, re.S | re.I)
    if fenced:
        text = fenced.group(1)
    # 去掉明确的对话开场；标题前如有业务警示则保留，由校验提示修正开场位置。
    title = re.search(r"^#{1,2}\s+[^\n]*(?:分析报告|排查报告|风险报告)[^\n]*$", text, re.M)
    if title and re.search(CHATTER, text[:title.start()]):
        prefix = text[:title.start()]
        paragraphs = re.split(r"\n\s*\n", prefix)
        prefix = "\n\n".join(p for p in paragraphs if not re.search(CHATTER, p)).strip()
        # 单独的分隔线不属于业务正文。
        prefix = re.sub(r"(?m)^\s*---+\s*$", "", prefix).strip()
        text = (prefix + "\n\n" if prefix else "") + text[title.start():]
    # 旧版关联交易模板将程序审计JSON列为第八章；仅移除明确的内部审计段。
    # 不删除任意代码块：风险证据或未知代码仍应被交付校验拦截。
    text = re.sub(r"(?ms)^#{1,6}\s+(?:(?:[一二三四五六七八九十]+|\d+)[、.．]\s*)?(?:审计日志|审计追踪)\s*\n.*?(?=^#{1,6}\s|\Z)", "", text)
    # 不启用渲染器的原始 HTML；仅剥离常见展示包装，保留包装内全部风险文字。
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"&lt;(/?(?:div|span|p|br|strong|b|em|i|section|aside)\b.*?)&gt;", r"<\1>", text, flags=re.I)
    text = re.sub(r"</?(?:div|p|section|aside|br)\b[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"</?(?:span|strong|b|em|i)\b[^>]*>", "", text, flags=re.I)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text, text != original


def presentation_errors(report: str, company_name: str = "") -> list[str]:
    errors = []
    if re.search(CHATTER, report):
        errors.append("正文含对话或修正说明，只能交付报告正文。")
    unwanted = list(re.finditer(TAG + r"|&lt;/?[A-Za-z]|```[^\n]*|\bstyle\s*=", report, re.I))
    if unwanted:
        details = "；".join(f"第{report.count(chr(10), 0, item.start()) + 1}行：{item.group()[:70]}" for item in unwanted[:3])
        errors.append("正文含HTML标签、样式或代码，须使用纯Markdown并保留业务警示。" + details)
    if re.search(r"(?:[XxＸ]{2,}|某某)[^\s|，。；：\n]{0,24}(?:公司|集团)|\[(?:客户名称|企业名称|关联方名称)\]", report):
        errors.append("企业名称含占位符，须使用数据源全称；全称缺失时明确说明，不得猜测。")
    if company_name and company_name not in report:
        errors.append("报告未出现本次调查企业全称：" + company_name)
    return errors

"""客户经理报告的结构与展示约束；直接从原始模板读取章节和表格。"""
import re
from pathlib import Path
from markdown_it import MarkdownIt


REPORT_TEMPLATE = Path(__file__).resolve().parents[2] / "assets" / "financial-report-template.md"
CLIENT_REPORT_RULES = """
【最终交付规则：客户经理阅读版；优先于参考资料中的展示要求】
最终只返回完整Markdown报告，严格填写所附 financial-report-template.md：
1. 保留模板全部八个一级章节、所有编号子章节及其顺序，标题逐字一致；不得增删、改名、合并。
2. 保留模板表格的指标行和非年度列，年度占位列替换为目标年度及以前最近三个可用年度。
   不得增加“公式、字段代码、来源字段、状态”等技术列。未知项目填“未提供/暂无法评价”，不能删除对应行或章节。
3. 报告面向客户经理，只呈现金额、指标结果、趋势、业务风险和建议。
   不展示公式、等式、计算过程、数据库/表名、SQL、接口名称、工具名称、系统字段代码、英文执行状态。
   不使用“数据库事实/公式计算结果/模型判断”等技术标签。
4. 1.3严格只写模板规定的四条：审计意见类型、会计政策变更、合并报表范围变化、数据完整性。
   总计不超过220字，每项一句；无需介绍数据获取链路、表结构、记录数或系统覆盖范围。
5. 在报告抬头用业务语言说明来源，如“信贷系统提供的企业财务报表（模拟数据）”；
   保留模拟数据、未审计等真实限制。指标口径差异用简短业务说明，如“归母净利润口径”或“偿债备付率为近似估算”。
   缺失资料只在对应模板位置简述，不另设“数据缺口/补充清单”章节，不反复解释。
6. 先写模板的核心结论、财务健康评级、红线警示；资料不足时标注“待核实”，不得将无法判断写为未触发。
   模板中的阈值只是分析参考，不能冒充已取得的行业均值。无依据不编造授信额度、期限、利率。
7. 杜邦分析只展示因素结果、变动贡献和业务解释，不展示推导。金额注明币种和单位，比例用百分数，倍数/天数明确单位。
8. 数据可追溯性通过内部审计留存；最终报告不输出内部技术细节。报告末尾保留模板的免责声明、分析人、复核人、报告日期。
"""


def load_template():
    return REPORT_TEMPLATE.read_text(encoding="utf-8")


def sections(text):
    """按模板编号标题拆分，保留标题及其下方正文。"""
    matches = list(re.finditer(r"^(#{2,6}) (.+)$", text, re.M))
    return [(m.group(0), text[m.end():matches[i + 1].start() if i + 1 < len(matches) else len(text)])
            for i, m in enumerate(matches)]


def table_rows(body):
    # 与HTML渲染器使用同一种Markdown语法，避免按字符串切割误读合法表格。
    tokens = MarkdownIt("commonmark").enable("table").parse(body)
    tables, current, row = [], None, None
    for token in tokens:
        if token.type == "table_open":
            current = []
        elif token.type == "tr_open" and current is not None:
            row = []
        elif token.type == "inline" and row is not None:
            row.append("".join(child.content for child in token.children or []
                               if child.type in {"text", "code_inline"}).strip())
        elif token.type == "tr_close" and row is not None:
            current.append(row)
            row = None
        elif token.type == "table_close" and current is not None:
            tables.append(current)
            current = None
    return tables


def normalize_client_report(report):
    """只统一Markdown书写差异，不修改财务数值、补写分析或删除风险。"""
    original = report
    text = report.strip()
    fenced = re.fullmatch(r"```(?:markdown|md)?\s*\n(.*?)\n```", text, re.S | re.I)
    if fenced:
        text = fenced.group(1)
    titles = {}
    for title, _ in sections(load_template()):
        content = title.lstrip("# ")
        key = re.match(r"(?:\d+\.\d+|[一二三四五六七八]+[、.．])", content)
        if key:
            titles[key.group(0).replace("．", "、").rstrip(".")] = title
    lines = []
    for line in text.splitlines():
        heading = re.match(r"^\s*#{2,6}\s*(.*?)\s*#*\s*$", line)
        if heading:
            content = heading.group(1).replace("**", "").strip()
            key = re.match(r"(?:\d+\.\d+|[一二三四五六七八]+[、.．])", content)
            if key and key.group(0).replace("．", "、").rstrip(".") in titles:
                line = titles[key.group(0).replace("．", "、").rstrip(".")]
            elif content == "免责声明":
                line = "**免责声明**"
        lines.append(line)
    # 只修改表头，不能把金额恰好为2025的单元格误改成“2025年”。
    tokens = MarkdownIt("commonmark").enable("table").parse("\n".join(lines))
    aliases = {"净资产收益率(ROE)": "净资产收益率", "净资产收益率（ROE）": "净资产收益率",
               "投入资本回报率(ROIC)": "投入资本回报率", "投入资本回报率（ROIC）": "投入资本回报率",
               "偿债备付率(DSCR)": "偿债备付率", "偿债备付率（DSCR）": "偿债备付率",
               "有息负债/EBITDA": "有息负债/息税折旧摊销前利润"}
    current_heading = ""
    for token in tokens:
        if token.type == "heading_open" and token.map:
            current_heading = lines[token.map[0]]
        if token.type != "table_open" or not token.map:
            continue
        start, end = token.map
        header = lines[start]
        header = "| " + header.strip().strip("|").strip() + " |"
        header = re.sub(r"(?<=\|)\s*(?:\*\*)?(20\d{2})\s*(?:年(?:度)?)?(?:\*\*)?\s*(?=\|)",
                        r" \1年 ", header)
        lines[start] = header
        # 明确等价的指标名称仅在相应指标表中规范化，不改变正文解释。
        if current_heading.startswith(("### 2.1 ", "### 4.2 ", "### 6.1 ")):
            for row_index in range(start + 2, end):
                match = re.match(r"^(\s*\|?\s*)([^|]+)(\|.*)$", lines[row_index])
                if match:
                    label = match[2].replace("**", "").strip()
                    if label in aliases:
                        lines[row_index] = match[1] + aliases[label] + " " + match[3]
    normalized = "\n".join(lines) + "\n"
    # 仅替换明确的数据缺失措辞，保留缺失原因，不掩盖实际字段代码或推导式。
    replacements = {
        "审计意见字段未在本次数据接口中接入": "审计意见资料未提供",
        "审计意见字段未接入本次数据接口": "审计意见资料未提供",
        "未接入数据库": "未提供",
        "未在本次字段映射中提供": "未提供",
        "数据库中未提供": "现有资料未提供",
        "数据库未提供": "现有资料未提供",
        "数据库无此字段": "相关资料未提供",
    }
    for source, target in replacements.items():
        normalized = normalized.replace(source, target)
    # 单个已算出的数值是结果陈述；带加减乘除的右侧表达式不匹配。
    normalized = re.sub(
        r"(?<=[\u4e00-\u9fffA-Z）)])\s*=\s*([+-]?\d[\d,]*(?:\.\d+)?(?:%|％|倍|天|次|万元|亿元|元)?)"
        r"(?![ \t]*[/+*×÷=−-])"
        r"(?=$|[\s，。；、,;）)])",
        r"为\1", normalized, flags=re.M,
    )
    return normalized, normalized.strip() != original.strip()


def _match_details(pattern, report, flags=0):
    details = []
    for match in list(re.finditer(pattern, report, flags))[:4]:
        line_number = report.count("\n", 0, match.start()) + 1
        line = report.splitlines()[line_number - 1].strip()
        details.append(f"第{line_number}行命中“{match.group()}”：{line[:110]}")
    return "；".join(details)


def report_skeleton(periods):
    """将模板中的年度占位列替换为真实可用年度，供模型直接填写。"""
    years = sorted(set(str(p) for p in periods))[-3:]
    text = load_template()
    if years:
        text = text.replace("[202X年-202X年]", f"{years[0]}年—{years[-1]}年")
    return re.sub(r"(?:\|\s*202X年\s*)+\|",
                  "| " + " | ".join(p + "年" for p in years) + " |", text) if years else text


def validate_client_report(report):
    errors = []
    template = load_template()
    expected = sections(template)
    actual = sections(report)
    if [title for title, _ in actual] != [title for title, _ in expected]:
        errors.append("章节或子章节不符合模板：须逐字保留全部编号标题、顺序，不得增删或合并。")
    if len(re.findall(r"^# .+", report, re.M)) != 1:
        errors.append("须有且仅有一个报告主标题。")
    bodies = dict(actual)
    for title, body in expected:
        if title not in bodies:
            continue
        if not bodies[title].replace("---", "").strip() and title.startswith("### "):
            errors.append(f"{title}：须填写内容，资料缺失时简述限制，不能只保留空标题。")
        expected_tables = table_rows(body)
        actual_tables = table_rows(bodies[title])
        if len(expected_tables) != len(actual_tables):
            errors.append(f"{title}：须保留模板的表格数量。")
            continue
        for wanted, got in zip(expected_tables, actual_tables):
            fixed_headers = lambda cells: [c for c in cells if not re.fullmatch(r"(?:20\d{2}|202X)年", c)]
            if fixed_headers(wanted[0]) != fixed_headers(got[0]):
                errors.append(f"{title}：表格非年度列须与模板一致，不得增加技术列。")
            if "202X年" in wanted[0]:
                years = [c for c in got[0] if re.fullmatch(r"20\d{2}年", c)]
                if not 1 <= len(years) <= 3 or years != sorted(set(years)):
                    errors.append(f"{title}：年度列须为一至三个不同年度，按时间升序排列。")
            if any(len(row) != len(got[0]) for row in got[1:]):
                errors.append(f"{title}：表格每行列数须与表头一致。")
            labels = [r[0] for r in got[1:]]
            required = [r[0] for r in wanted[1:] if not r[0].startswith("[") and r[0] != "..."]
            if required and labels != required:
                errors.append(f"{title}：指标行及顺序须与模板一致，缺失数据也须保留行并标注。")
    quality = bodies.get("### 1.3 数据来源与质量评估", "")
    lines = [line.strip() for line in quality.splitlines() if line.strip() and line.strip() != "---"]
    labels = ["审计意见类型", "会计政策变更", "合并报表范围变化", "数据完整性"]
    if len(lines) != 4 or any(label not in line for label, line in zip(labels, lines)) or len("".join(lines)) > 220:
        errors.append("1.3须只包含审计意见、会计政策、合并范围和完整性四条简述，总计不超过220字。")
    forbidden = r"MySQL|financial_statement_values|MOCK_CREDIT_SYSTEM_API|source_record|company_id|query_financial_data|calculate_financial_indicators|get_data_quality_summary|\b(?:SY\d+[\w]*|ZCFZ\d+[\w]*|XJ\d+|ANNEX\d+|CF\d+|INDEX\d+|available|unavailable|SQL|JSON|API)\b|数据库|字段代码|公式|计算过程|```"
    if re.search(forbidden, report, re.I):
        errors.append("报告含公式/字段代码/数据库或执行状态等技术细节，须改为客户经理业务表述。" + _match_details(forbidden, report, re.I))
    formula_pattern = r"(?<![<>!])=(?!=)|÷|\\frac|×\s*[\dA-Za-z\u4e00-\u9fff(（]"
    if re.search(formula_pattern, report):
        errors.append("报告含等式或算式，仅保留计算结果和业务解释。" + _match_details(formula_pattern, report))
    placeholders = re.findall(r"\[[^\]\n]+\]", template)
    if any(item in report for item in placeholders) or "202X年" in report or "YYYY-MM-DD" in report:
        errors.append("报告含未填写的模板占位符；资料未知时须填写未提供或待核实。")
    for label in ["企业名称", "股票代码", "分析日期", "分析场景", "报告期间", "数据来源", "数据日期", "分析师"]:
        if not re.search(r"\*\*" + label + r"\*\*[：:]\s*\S", report):
            errors.append(f"缺少模板抬头项目：{label}。")
    for label in ["核心结论", "财务健康评级", "红线警示", "免责声明", "分析人", "复核人", "报告日期"]:
        if label not in report:
            errors.append(f"缺少模板项目：{label}。")
    return errors


def require_client_report(report):
    errors = validate_client_report(report)
    if errors:
        raise ValueError("客户经理报告未通过模板校验：" + "；".join(errors))

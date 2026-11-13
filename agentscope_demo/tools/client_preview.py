"""依原始模板生成离线排版预览；不生成大模型分析或审批判断。"""
from datetime import datetime, timedelta, timezone

from tools.report_contract import load_template, sections, table_rows


PERCENT_INDICATORS = {"毛利率", "核心利润率", "净利率", "净资产收益率（ROE）", "投入资本回报率（ROIC）", "核心利润占比", "资产负债率", "有息负债率"}
METRICS = {
    "净资产收益率": "净资产收益率（ROE）", "投入资本回报率": "投入资本回报率（ROIC）",
    "偿债备付率": "偿债备付率（DSCR）", "有息负债/息税折旧摊销前利润": "有息负债/EBITDA",
}
RAW_FIELDS = {"应收账款余额": "ZCFZ06", "存货余额": "ZCFZ15", "经营现金流": "XJ25", "资本开支": "XJ32"}


def offline_report(company, target, results, scenario, operator="未填写"):
    target = str(target)
    if target not in results:
        raise ValueError(f"没有报告期 {target} 的财务数据")
    periods = sorted(p for p in results if p <= target)[-3:]
    today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    template = load_template()
    header = template.split("## 一、", 1)[0]
    for before, after in {
        "# 财务分析报告模板": f"# {company['company_name']} 财务分析报告",
        "[企业全称]": company["company_name"], "[如适用]": "未提供",
        "[贷前尽调 / 年度贷后检视 / 大额授信审批 / 重组/展期评估 / 投资决策参考]": scenario,
        "[202X年-202X年]": f"{periods[0]}年—{periods[-1]}年",
        "[年报/季报/审计报告/附注]": "企业财务报表（模拟数据，仅供离线排版预览）",
        "[姓名（工号）]": operator,
        "[用2-3句话概括最重要的发现]": "本报告为模拟数据的离线排版预览，展示已计算的财务指标。尚未开展完整财务分析，不能据此作出授信判断。",
        "[优秀/良好/一般/关注/警示]（基于综合评估）": "暂不评级（离线预览）",
        '（如触发8条红线之一，在此醒目标注）': '',
        '[如触发，列出红线编号及简要说明；如无则写"本次分析未触发红线条件"]': "红线尚未完成核查，不能认定未触发。",
    }.items():
        header = header.replace(before, after)
    header = header.replace("**数据日期**：YYYY-MM-DD", "**数据日期**：未提供").replace("YYYY-MM-DD", today)

    def value(label, period):
        data = results[period]
        if label in RAW_FIELDS:
            amount = data["raw_fields"].get(RAW_FIELDS[label])
            return "未提供" if amount is None else f"{amount:,.2f}元"
        name = METRICS.get(label, label)
        item = data["指标"].get(name)
        if not item or item["value"] is None:
            return "未提供"
        val = item["value"]
        if name in PERCENT_INDICATORS:
            return f"{val * 100:.2f}%"
        unit = item["unit"] if item["unit"] in {"倍", "天", "次"} else ""
        return f"{val:.2f}{unit}"

    def fill_table(rows):
        old_header = rows[0]
        first_year = next((i for i, c in enumerate(old_header) if c == "202X年"), None)
        if first_year is None:
            mapped = [old_header]
            if old_header == ["项目", "内容"]:
                for row in rows[1:]:
                    mapped.append([row[0], company.get("industry_name", "未提供") if row[0] == "行业分类" else "未提供"])
            else:
                mapped.append(["待开展完整分析"] + ["待核实"] * (len(old_header) - 1))
        else:
            last_year = max(i for i, c in enumerate(old_header) if c == "202X年")
            mapped = [old_header[:first_year] + [p + "年" for p in periods] + old_header[last_year + 1:]]
            for row in rows[1:]:
                label = row[0]
                if label.startswith("[") or label == "...":
                    continue
                tail = []
                for index in range(last_year + 1, len(old_header)):
                    heading = old_header[index]
                    original = row[index] if index < len(row) else ""
                    tail.append(original if heading in {"安全阈值", "评价标准"} and original and not original.startswith("[") else "未提供" if heading == "行业均值" else "暂无法评价")
                mapped.append([label] + [value(label, p) for p in periods] + tail)
            if len(mapped) == 1:
                # 模板的动态预警/跟踪表，明确未分析，避免给出虚构评级。
                mapped.append(["待开展完整分析"] + ["待核实"] * (len(mapped[0]) - 1))
        return "\n".join(
            ["| " + " | ".join(mapped[0]) + " |", "| " + " | ".join(["---"] * len(mapped[0])) + " |"]
            + ["| " + " | ".join(row) + " |" for row in mapped[1:]]
        )

    notes = {
        "### 1.2 本次分析重点": "关注盈利质量、偿债能力与现金流。当前仅为模拟数据的离线排版预览，各项风险判断待完整分析后补充。",
        "### 1.3 数据来源与质量评估": "- 审计意见类型：未提供，待核实。\n- 会计政策变更：未提供。\n- 合并报表范围变化：未提供。\n- 数据完整性：采用模拟数据，审计报告、附注和部分明细缺失。",
        "### 2.1 核心盈利指标": "金额单位为人民币元。净利润相关指标采用归母净利润口径，非扣非口径。行业均值未提供，暂不作同业比较。",
        "### 4.2 偿债能力指标": "偿债备付率为近似估算：尚未区分维持性与扩张性资本开支，年度应还本息口径待核实。",
        "### 7.2 报表勾稽关系验证": "- 资产负债表与现金流量表核对：明细不足，暂无法验证。\n- 净利润与权益变动核对：缺少权益变动明细，暂无法验证。",
        "### 8.1 财务健康度总结": "暂不评级。本报告为模拟数据的离线排版预览，不代表完整财务分析结论。",
    }
    parts = [header.rstrip()]
    template_sections = sections(template)
    for index, (heading, body) in enumerate(template_sections):
        parts.append(heading)
        tables = table_rows(body)
        parts.extend(fill_table(table) for table in tables)
        if heading.startswith("### "):
            parts.append(notes.get(heading, "相关资料未提供或尚未完成分析，暂无法评价。"))
        if index == len(template_sections) - 1:
            parts.append(f"---\n\n**免责声明**：本报告基于模拟财务数据，仅供离线排版预览，不构成投资建议或授信审批决定。所有结论需经专业人士复核。\n\n**分析人**：{operator}  \n**复核人**：待复核  \n**报告日期**：{today}")
    return "\n\n".join(parts) + "\n"

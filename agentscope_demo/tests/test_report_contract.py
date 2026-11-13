import asyncio
from types import SimpleNamespace

import pytest

from mock_source import build_payload
from orchestration import agent_factory
from tools.client_preview import offline_report
from tools.indicator_calculator import calculate_all
from tools.report_contract import require_client_report, validate_client_report, normalize_client_report, table_rows, report_skeleton


@pytest.fixture(autouse=True)
def diagnostic_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_factory, "DIAGNOSTICS", tmp_path / "diagnostics")
    # 这些用例只覆盖最终报告交付修正；Skill 分阶段调用由 test_skill_steps 单独覆盖。
    async def no_steps(*args):
        return None
    monkeypatch.setattr(agent_factory, "execute_skill_steps", no_steps)


@pytest.fixture
def report():
    payload = build_payload()
    return offline_report(payload["company"], "2025", calculate_all(payload["records"]), "贷前尽调")


def test_preview_follows_original_template(report):
    assert validate_client_report(report) == []
    assert "| 毛利率 | 29.60% | 29.80% | 30.00% |" in report
    assert "SY02" not in report
    assert "## 九、" not in report


@pytest.mark.parametrize("bad_text", [
    "MySQL财务原始科目表", "SY02", "ANNEX13", "unavailable", "source_record_id", "净利率=净利润/营业收入", "ROE = 1.2", "100 ÷ 2", "公式：净利润除以收入", "净利率 × 总资产周转率",
])
def test_technical_details_are_rejected(report, bad_text):
    with pytest.raises(ValueError, match="模板校验"):
        require_client_report(report + "\n" + bad_text)


@pytest.mark.parametrize("before, after", [
    ("### 4.4 期限匹配分析", "### 4.4 期限匹配"),
    ("| 指标 | 2023年 | 2024年 | 2025年 | 行业均值 | 评价 |", "| 指标 | 公式 | 2023年 | 2024年 | 2025年 | 行业均值 | 评价 |"),
    ("| 投入资本回报率 |", "| 其他指标 |"),
    ("**企业名称**：示例制造股份有限公司", "**企业名称**：[企业全称]"),
    ("- 会计政策变更：未提供。", "- 会计政策变更：" + "数据获取链路。" * 60),
])
def test_template_deviations_are_rejected(report, before, after):
    assert validate_client_report(report.replace(before, after))


def test_single_year_preview_keeps_missing_rows():
    payload = build_payload()
    results = calculate_all(payload["records"])
    report = offline_report(payload["company"], "2022", {"2022": results["2022"]}, "贷前尽调")
    assert validate_client_report(report) == []
    assert "| 净资产收益率 | 未提供 |" in report


def test_agent_repairs_invalid_report_before_return(monkeypatch, report):
    prompts = []

    async def reply(message):
        prompts.append(message)
        text = "# 不符合模板的报告\n公式：SY02" if len(prompts) == 1 else report
        return SimpleNamespace(get_text_content=lambda: text)

    toolbox = SimpleNamespace()
    monkeypatch.setattr(agent_factory, "build_financial_agent", lambda *_: (SimpleNamespace(reply=reply), toolbox))
    result, _ = asyncio.run(agent_factory.run_financial_agent("DEMO001", "示例制造股份有限公司", "2025", "贷前尽调", "操作人"))
    assert result == report
    assert len(prompts) == 2
    assert toolbox.report_validation_events[0]["errors"]
    assert toolbox.report_validation_events[1]["errors"] == []


def test_agent_stops_after_two_unsuccessful_repairs(monkeypatch):
    async def reply(_):
        return SimpleNamespace(get_text_content=lambda: "# 不符合模板的报告")

    toolbox = SimpleNamespace()
    monkeypatch.setattr(agent_factory, "build_financial_agent", lambda *_: (SimpleNamespace(reply=reply), toolbox))
    with pytest.raises(ValueError, match="两次修正"):
        asyncio.run(agent_factory.run_financial_agent("DEMO001", "示例制造股份有限公司", "2025", "贷前尽调", "操作人"))
    assert len(toolbox.report_validation_events) == 3


def test_model_markdown_variations_do_not_require_another_model_call(monkeypatch, report):
    variant = report.replace("2023年 |", "2023 年 |")
    variant = variant.replace("2024年 |", "2024 |")
    variant = variant.replace("2025年 |", "**2025年度** |")
    variant = variant.replace("### 2.1 核心盈利指标", "### **2.1 核心盈利指标**")
    variant = variant.replace("| 净资产收益率 |", "| 净资产收益率（ROE） |")
    variant = "```markdown\n" + variant + "\n```"
    prompts = []

    async def reply(message):
        prompts.append(message)
        return SimpleNamespace(get_text_content=lambda: variant)

    toolbox = SimpleNamespace()
    monkeypatch.setattr(agent_factory, "build_financial_agent", lambda *_: (SimpleNamespace(reply=reply), toolbox))
    output, _ = asyncio.run(agent_factory.run_financial_agent("DEMO001", "示例制造股份有限公司", "2025", "贷前尽调", "操作人"))
    assert validate_client_report(output) == []
    assert len(prompts) == 1
    from pathlib import Path
    assert (Path(toolbox.report_diagnostics) / "attempt_1.raw.md").read_text() == variant


def test_year_normalization_does_not_modify_amounts(report):
    report = report.replace("| 存货余额 | 16,200,000.00元 |", "| 存货余额 | 2025 |")
    output, _ = normalize_client_report(report)
    assert "| 存货余额 | 2025 |" in output
    assert "| 存货余额 | 2025年 |" not in output


def test_markdown_table_escaped_pipe():
    assert table_rows("| 指标 | 数值 |\n|---|---|\n| A\\|B | 2025 |") == [
        [["指标", "数值"], ["A|B", "2025"]]
    ]


def test_skeleton_uses_available_years():
    skeleton = report_skeleton(["2022", "2023", "2024", "2025"])
    assert "202X年" not in skeleton
    assert "| 指标 | 2023年 | 2024年 | 2025年 | 行业均值 | 评价 |" in skeleton


def test_normalization_does_not_hide_real_errors(report):
    normalized, _ = normalize_client_report(report + "\n净利率=SY31/SY02\n")
    assert validate_client_report(normalized)


def test_customer_wording_from_failed_report_is_normalized(report):
    note = (
        "附注未接入数据库。审计意见字段未接入本次数据接口。"
        "数据库未提供并购支出相关数据。数据库无此字段。"
        "其他应收款/净资产=4.62%，远低于阈值，且缺少关联方拆分 → 待核实。"
    )
    output, changed = normalize_client_report(report + "\n" + note)
    assert changed
    assert validate_client_report(output) == []
    assert "4.62%" in output and "缺少关联方拆分 → 待核实" in output
    assert "附注未提供" in output and "审计意见资料未提供" in output
    assert "其他应收款/净资产为4.62%" in output


@pytest.mark.parametrize("expression", ["利润=100-50", "利润=100 / 50", "利润=100 × 2", "利润=100 + 50", "净利率=SY31/SY02"])
def test_real_formulas_cannot_be_normalized_into_results(report, expression):
    output, _ = normalize_client_report(report + "\n" + expression)
    assert validate_client_report(output)


def test_validation_points_to_actual_line(report):
    output = report + "\nSY31/SY02\n"
    error = "；".join(validate_client_report(output))
    assert "行命中“SY31”" in error and "SY31/SY02" in error


def test_extra_roa_row_keeps_values_as_supplement_without_breaking_template(report):
    extra = "| 总资产收益率 | 8.45% | 8.44% | 8.44% | 未提供 | 稳定 |\n"
    variant = report.replace("| 投入资本回报率 |", extra + "| 投入资本回报率 |", 1)
    assert any("多余：总资产收益率" in e for e in validate_client_report(variant))
    normalized, changed = normalize_client_report(variant)
    assert changed and validate_client_report(normalized) == []
    assert "2023年：8.45%" in normalized and "2024年：8.44%" in normalized
    assert "补充指标——总资产收益率" in normalized
    assert "| 总资产收益率 |" not in normalized
    assert normalize_client_report(normalized)[0] == normalized


def test_unrecognized_extra_metric_still_fails_with_specific_reason(report):
    variant = report.replace("| 投入资本回报率 |", "| 自造盈利指标 | 1% | 2% | 3% | 未提供 | 待核实 |\n| 投入资本回报率 |", 1)
    normalized, _ = normalize_client_report(variant)
    assert any("多余：自造盈利指标" in e for e in validate_client_report(normalized))

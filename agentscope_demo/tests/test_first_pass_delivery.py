import json
from pathlib import Path
from types import SimpleNamespace

from orchestration import agent_factory
from orchestration.analysis_pipeline import REGISTRY
from tools.report_instructions import analysis_instructions
from tools.related_party_contract import load_template, validate_related_party_report
from tools.report_presentation import normalize_presentation, presentation_errors


def test_customer_template_does_not_demand_internal_audit_json():
    template = load_template()
    assert "审计日志" not in template and "```" not in template
    assert "免责声明" in template
    for node in REGISTRY.values():
        text = analysis_instructions((Path(__file__).resolve().parents[2] / node.skill / "SKILL.md").read_text())
        assert "```" not in text and "Mermaid" not in text
        assert "审计追踪\n" not in text


def test_old_template_audit_block_is_removed_without_dropping_business_evidence():
    headings = ["一、报告基本信息", "二、关联方清单", "三、关联交易异常特征", "四、关联风险评估", "五、风险分级与处置建议", "六、一票否决触发情况", "七、数据缺失与降级处理"]
    report = "# 示例企业关联风险排查报告\n" + "\n".join("### " + heading for heading in headings)
    report += '\n征信系统不可用，担保金额未提供。\n### 八、审计日志\n```json\n{"operator":"tester","skill_name":"credit-related-party-detection"}\n```\n### 免责声明\n仅供授信参考。'
    normalized, changed = normalize_presentation(report)
    assert changed
    assert validate_related_party_report(normalized) == []
    assert "担保金额未提供" in normalized and "仅供授信参考" in normalized
    assert "operator" not in normalized
    # 未知的正文代码依然拒绝，不能通过放宽校验让报告假通过。
    assert presentation_errors(normalized + '\n```python\nprint(1)\n```')


def test_report_writer_is_fresh_and_receives_only_delivery_years(monkeypatch):
    original = SimpleNamespace(_system_prompt="步骤必须返回JSON", model=object(), toolkit=object())
    captured = []
    def factory(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(**kwargs)
    monkeypatch.setattr(agent_factory, "Agent", factory)
    toolbox = SimpleNamespace(rows=[{"report_period": str(year), "amount": year} for year in range(2022, 2027)],
        calculated={str(year): {"value": year} for year in range(2022, 2027)},
        skill_step_events=[{"summary": "业务摘要"}])
    writer, prompt = agent_factory.prepare_report_writer(original, "financial", toolbox, "模板骨架", report_period="2025")
    assert writer is not original and writer.model is original.model
    assert "步骤必须返回JSON" not in writer.system_prompt
    assert "不要输出审计日志" in writer.system_prompt
    data = json.loads(prompt.split("【分析资料，只供撰写；内部字段不得出现在正文】\n")[1])
    assert data["delivery_years"] == ["2023", "2024", "2025"]
    assert set(data["calculated"]) == {"2023", "2024", "2025"}
    assert toolbox.rows[0]["report_period"] == "2022"  # 不修改原始分析数据。


def test_inline_fence_mention_does_not_swallow_business_rules():
    text = "## 输出格式\n不要使用```json标记。\n## 核心约束\n不得虚构风险。\n```json\n{\"example\": true}\n```\n## 数据缺失\n征信未接入。"
    cleaned = analysis_instructions(text)
    assert "不得虚构风险" in cleaned and "征信未接入" in cleaned
    assert "```" not in cleaned

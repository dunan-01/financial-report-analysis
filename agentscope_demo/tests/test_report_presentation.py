import asyncio
from types import SimpleNamespace

import pytest

from mock_source import build_related_party_payload
from orchestration import agent_factory
from tools.related_party_contract import load_template, validate_related_party_report
from tools.report_presentation import normalize_presentation, presentation_errors
from tools.report_renderer import render_combined_html


DIRTY = '''您指出的问题非常清楚。上一份报告中章节不正确，我已重新生成。

---

# 关联交易识别与关联风险排查报告

<div style="color:red; border:3px solid red;">

> **【一票否决条件 R4 已触发】— 循环担保圈风险**
> 担保圈4家，链条3层，建议暂停授信。

</div>
'''


def test_clean_delivery_preserves_risk_facts_and_renders_warning():
    clean, changed = normalize_presentation(DIRTY)
    assert changed
    assert clean.startswith("# 关联交易")
    assert "<div" not in clean and "</div>" not in clean
    assert "您指出" not in clean
    assert "担保圈4家，链条3层，建议暂停授信。" in clean
    assert not presentation_errors(clean)
    html = render_combined_html(financial_report="# 财报", related_party_report=clean,
        company_name="示例制造股份有限公司", report_period="2025", scenario="贷前尽调", generated_at="2026-11-12")
    assert '<blockquote class="risk-warning">' in html
    assert '&lt;div style=' not in html


@pytest.mark.parametrize("text", ["XX钢铁贸易有限公司", "某某投资有限公司", "我已重新修正报告", "<script>alert(1)</script>", "```python\nprint(1)\n```"])
def test_unacceptable_delivery_is_rejected(text):
    assert presentation_errors(text)


def test_no_guessed_company_names_and_mock_uses_complete_names():
    payload = build_related_party_payload(company_name="测试华明装备股份有限公司")
    assert payload["customer_info"]["company_name"] == "测试华明装备股份有限公司"
    assert all("XX" not in party["name"] for party in payload["related_parties"])
    assert presentation_errors("# 关联风险报告", "测试华明装备股份有限公司")
    assert "关联方风险排查报告" in load_template()


def test_related_agent_normalizes_and_retries_placeholders(monkeypatch, tmp_path):
    prompts = []
    # 模板标题和章节用来构造结构完整的模型回复，替换所有示意占位符。
    sections = ["一、报告基本信息", "二、关联方清单", "三、关联交易异常特征", "四、关联风险评估",
                "五、风险分级与处置建议", "六、一票否决触发情况", "七、数据缺失与降级处理"]
    body = "\n".join("### " + s for s in sections)

    async def reply(message):
        prompts.append(message)
        name = "XX公司" if len(prompts) == 1 else "示例华远钢铁贸易有限公司"
        return SimpleNamespace(get_text_content=lambda: DIRTY + "\n测试华明装备股份有限公司\n" + name + "\n" + body)

    async def no_steps(*args):
        return None
    monkeypatch.setattr(agent_factory, "execute_skill_steps", no_steps)
    toolbox = SimpleNamespace()
    monkeypatch.setattr(agent_factory, "build_related_party_agent", lambda *args: (SimpleNamespace(reply=reply), toolbox, {}))
    monkeypatch.setattr(agent_factory, "DIAGNOSTICS", tmp_path)
    report, _ = asyncio.run(agent_factory.run_related_party_agent("DEMO001", "测试华明装备股份有限公司", "贷前尽调", "tester"))
    assert len(prompts) == 2
    assert validate_related_party_report(report) == []
    assert "XX公司" not in report and "style=" not in report and "您指出" not in report
    assert list(tmp_path.rglob("rp_attempt_1.raw.md"))
    assert list(tmp_path.rglob("rp_attempt_2.normalized.md"))

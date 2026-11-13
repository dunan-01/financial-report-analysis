import asyncio
import json
from types import SimpleNamespace

import pytest

from orchestration.agent_factory import execute_skill_steps
from orchestration.progress_events import get_skill_steps, get_workflows, progress_scope, redact_audit_text
from orchestration.analysis_pipeline import REGISTRY


def test_steps_are_read_from_all_nine_real_skill_files():
    counts = {"industry": 10, "policy": 6, "risk_evidence": 6, "verification": 7,
              "due_diligence": 10, "equity": 10, "financial": 10, "related_party": 4, "risk_extraction": 7}
    for key in REGISTRY:
        steps = get_skill_steps(key)
        assert len(steps) == counts[key]
        assert len({step["id"] for step in steps}) == len(steps)
        assert all("数据来源" not in step["label"] and "确认机制" not in step["label"] for step in steps)
        assert all(step["instructions"].strip() for step in steps)
        plan = dict(get_workflows()[key])
        assert all(step["id"] in plan for step in steps)


def test_business_steps_really_execute_in_order_with_limited_results(tmp_path):
    messages, events = [], []
    steps = get_skill_steps("policy")
    async def reply(message):
        index = len(messages)
        assert steps[index]["label"] in message.content[0].text
        messages.append(message)
        result = dict(status="insufficient_data" if index == 1 else "completed",
                      summary="未提供实时政策，不能进行真实政策核验。", evidence_refs=["query_skill_data"])
        return SimpleNamespace(get_text_content=lambda: json.dumps(result, ensure_ascii=False))
    toolbox = SimpleNamespace()
    async def check():
        with progress_scope(sink=events.append, agent="policy"):
            await execute_skill_steps(SimpleNamespace(reply=reply), "policy", "企业材料", tmp_path, toolbox)
    asyncio.run(check())
    assert len(messages) == 6
    assert [e["step"] for e in events if e["status"] == "running"] == [step["id"] for step in steps]
    assert [e["status"] for e in toolbox.skill_step_events] == ["succeeded", "limited", "succeeded", "succeeded", "succeeded", "succeeded"]
    assert len(list(tmp_path.glob("*.result.json"))) == 6
    assert all(e["summary_origin"] == "model_reported" for e in toolbox.skill_step_events)


def test_failed_step_does_not_mark_later_steps_completed(tmp_path):
    calls, events = [], []
    async def reply(message):
        calls.append(message)
        return SimpleNamespace(get_text_content=lambda: "不是有效步骤结果")
    async def check():
        with progress_scope(sink=events.append):
            with pytest.raises(ValueError, match="有效的步骤"):
                await execute_skill_steps(SimpleNamespace(reply=reply), "policy", "", tmp_path, SimpleNamespace())
    asyncio.run(check())
    assert len(calls) == 2  # 当前步骤格式修正一次，不继续执行后续阶段。
    assert [e["status"] for e in events] == ["running", "retrying", "failed"]


def test_audit_summary_does_not_expose_credentials_or_personal_numbers():
    text = "api_key=abc-secret Bearer abcdef sk-123456789ABCDE 电话13800138000"
    result = redact_audit_text(text)
    assert "abc-secret" not in result and "abcdef" not in result and "13800138000" not in result


def test_preamble_and_json_fence_preserve_insufficient_data():
    from orchestration.agent_factory import parse_step_result
    raw = '根据接口数据，资料不完整。\n\n```json\n{"status":"insufficient_data","summary":"行业基准未提供，无法核验。","evidence_refs":["query_skill_data"]}\n```'
    result, method = parse_step_result(raw)
    assert result["status"] == "insufficient_data"
    assert result["summary"] == "行业基准未提供，无法核验。"
    assert method == "extracted_json"


def test_unescaped_chinese_quotes_are_syntax_repaired_not_business_rewritten():
    from orchestration.agent_factory import parse_step_result
    raw = '根据材料缺少对标数据。\n{"status":"insufficient_data","summary":"接口标注"未取得行业对标数据"，无法验证。","evidence_refs":["用户材料"]}'
    result, method = parse_step_result(raw)
    assert result["status"] == "insufficient_data"
    assert "未取得行业对标数据" in result["summary"]
    assert "无法验证" in result["summary"]
    assert method == "json_repaired"


@pytest.mark.parametrize("raw", [
    '{"status":"unknown","summary":"不应默认通过"}',
    '{"summary":"缺少状态，不能补成completed"}',
    '{"status":"completed","summary":"", "evidence_refs":[]}',
    '{"status":"completed","summary":"有效摘要", "evidence_refs":[123]}',
])
def test_syntax_tolerance_does_not_invent_or_weaken_business_fields(raw):
    from orchestration.agent_factory import parse_step_result
    with pytest.raises(ValueError):
        parse_step_result(raw)


def test_native_structured_output_is_requested_and_used(tmp_path):
    calls = []
    async def reply(message, structured_schema=None):
        assert structured_schema is not None
        calls.append(structured_schema)
        return SimpleNamespace(structured_output={"status":"insufficient_data", "summary":"资料不足。", "evidence_refs":[]},
                               get_text_content=lambda: "", finished_reason="completed")
    toolbox = SimpleNamespace()
    asyncio.run(execute_skill_steps(SimpleNamespace(reply=reply), "policy", "", tmp_path, toolbox))
    assert len(calls) == len(get_skill_steps("policy"))
    assert all(e["status"] == "limited" and e["parse_method"] == "native_structured" for e in toolbox.skill_step_events)


def test_swallowed_model_cancellation_stays_cancelled_not_format_failure(tmp_path):
    events = []
    async def reply(message):
        asyncio.current_task().cancel()
        try:
            await asyncio.sleep(0)
        except asyncio.CancelledError:
            return SimpleNamespace(get_text_content=lambda: "I notice the interruption. How can I help you?", finished_reason="interrupted")
    async def check():
        with progress_scope(sink=events.append):
            task = asyncio.create_task(execute_skill_steps(SimpleNamespace(reply=reply), "policy", "", tmp_path, SimpleNamespace()))
            with pytest.raises(asyncio.CancelledError):
                await task
    asyncio.run(check())
    assert [e["status"] for e in events] == ["running", "cancelled"]

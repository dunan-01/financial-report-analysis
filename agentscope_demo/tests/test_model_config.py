import asyncio
import json
import sys
from types import SimpleNamespace

import pytest

import run_demo
from mock_source import build_payload
from orchestration import agent_factory, analysis_pipeline
from orchestration.model_settings import load_model_settings, normalize_base_url


@pytest.fixture(autouse=True)
def clear_enterprise_config(monkeypatch):
    for name in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY", "LLM_STREAM"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("configured, expected", [
    (None, "openrouter/free"),
    ("", "openrouter/free"),
    (" vendor/test-model ", "vendor/test-model"),
])
def test_model_client_and_audit_use_same_config(monkeypatch, tmp_path, capsys, configured, expected):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://example.test/api/v1")
    monkeypatch.setenv("DASHSCOPE_MODEL", "obsolete-model")
    if configured is None:
        monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    else:
        monkeypatch.setenv("OPENROUTER_MODEL", configured)
    # 构建真实工具配置，替换模型客户端和 Agent，避免任何网络调用。
    monkeypatch.setattr(agent_factory, "OpenAIChatModel", lambda **kw: SimpleNamespace(**kw))
    monkeypatch.setattr(agent_factory, "OpenAICredential", lambda **kw: SimpleNamespace(**kw))
    monkeypatch.setattr(agent_factory, "Agent", lambda **kw: SimpleNamespace(**kw))
    agent, toolbox = agent_factory.build_financial_agent("DEMO001")
    assert agent.model.model == expected
    assert agent.model.credential.base_url == "https://example.test/api/v1"

    async def fake_run_agent(*args):
        toolbox.calculated = run_demo.calculate_all(payload["records"])
        toolbox.report_validation_events = [{"attempt": 1, "errors": []}]
        return run_demo.offline_report(payload["company"], "2025", toolbox.calculated, "贷前尽调"), toolbox

    payload = build_payload()
    async def fake_run_rp(*args):
        return "", SimpleNamespace(audit_events=[], report_validation_events=[])
    monkeypatch.setattr(analysis_pipeline, "run_financial_agent", fake_run_agent)
    monkeypatch.setattr(analysis_pipeline, "run_related_party_agent", fake_run_rp)
    async def fake_registered(*args, **kwargs):
        return "# 示例制造股份有限公司 技能报告", SimpleNamespace(audit_events=[], report_validation_events=[])
    monkeypatch.setattr(agent_factory, "run_registered_skill", fake_registered)

    monkeypatch.setattr(run_demo, "load_dotenv", lambda: None)
    monkeypatch.setattr(run_demo, "get_financial_data", lambda *_, **__: payload)
    monkeypatch.setattr(run_demo, "get_company_info", lambda _: payload["company"])
    monkeypatch.setattr(run_demo, "get_financial_records", lambda _: payload["records"])
    monkeypatch.setattr(run_demo, "BASE", tmp_path)
    monkeypatch.setattr(run_demo, "RUNS", tmp_path)
    monkeypatch.setattr(sys, "argv", ["run_demo.py", "--company-id", "DEMO001", "--report-period", "2025"])
    asyncio.run(run_demo.main())
    summary_path = next(tmp_path.glob("*/summary.json"))
    audit = json.loads(summary_path.read_text())
    assert audit["model"] == agent.model.model
    html_path = summary_path.parent / audit["html_report_path"]
    assert html_path.exists()
    assert "示例制造股份有限公司" in html_path.read_text()
    assert audit["financial_report_path"]
    capsys.readouterr()


@pytest.mark.parametrize("key", [None, "   "])
def test_missing_key_names_openrouter(monkeypatch, key):
    if key is None:
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    else:
        monkeypatch.setenv("OPENROUTER_API_KEY", key)
    with pytest.raises(RuntimeError, match="缺少 OPENROUTER_API_KEY"):
        agent_factory.build_financial_agent("DEMO001")


@pytest.mark.parametrize("url", [
    "http://intranet.example:8000/v1",
    "http://intranet.example:8000/v1/",
    "http://intranet.example:8000/v1/chat/completions",
    "http://intranet.example:8000/v1/chat/completions/",
])
def test_enterprise_model_receives_normalized_endpoint(monkeypatch, url):
    monkeypatch.setenv("LLM_BASE_URL", url)
    monkeypatch.setenv("LLM_MODEL", "deepseek-enterprise")
    monkeypatch.setenv("LLM_API_KEY", "enterprise-test-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "obsolete-key")
    monkeypatch.setattr(agent_factory, "OpenAIChatModel", lambda **kw: SimpleNamespace(**kw))
    monkeypatch.setattr(agent_factory, "OpenAICredential", lambda **kw: SimpleNamespace(**kw))
    monkeypatch.setattr(agent_factory, "Agent", lambda **kw: SimpleNamespace(**kw))
    agent, toolbox = agent_factory.build_financial_agent("DEMO001")
    assert agent.model.credential.base_url == "http://intranet.example:8000/v1"
    assert agent.model.model == toolbox.model_name == "deepseek-enterprise"
    assert agent.model.credential.api_key.get_secret_value() == "enterprise-test-key"
    assert agent.model.stream is False
    assert "enterprise-test-key" not in repr(load_model_settings())


def test_partial_enterprise_settings_do_not_fall_back(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://intranet.example/v1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "obsolete-key")
    with pytest.raises(RuntimeError, match="LLM_MODEL.*LLM_API_KEY"):
        load_model_settings()


@pytest.mark.parametrize("url", ["invalid", "ftp://example.test/v1", "https://example.test/v1?key=x"])
def test_invalid_model_url(url):
    with pytest.raises(ValueError):
        normalize_base_url(url)


def test_enterprise_stream_flag(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://intranet.example/v1")
    monkeypatch.setenv("LLM_MODEL", "deepseek-enterprise")
    monkeypatch.setenv("LLM_API_KEY", "enterprise-test-key")
    monkeypatch.setenv("LLM_STREAM", "true")
    assert load_model_settings().stream is True


@pytest.mark.parametrize("builder", [agent_factory.build_financial_agent, agent_factory.build_related_party_agent])
def test_real_agent_model_constructor_uses_credential_endpoint(monkeypatch, builder):
    # 不替换模型/凭证类：覆盖真实 AgentScope → OpenAI 客户端构造路径。
    # 初始化不会发起模型请求，可在无真实密钥、无企业网络时复现参数冲突。
    monkeypatch.setenv("LLM_BASE_URL", "http://example.test:8000/v1/chat/completions")
    monkeypatch.setenv("LLM_MODEL", "constructor-test")
    monkeypatch.setenv("LLM_API_KEY", "not-a-real-key")
    agent = builder("DEMO001")[0]
    assert agent.model.credential.base_url == "http://example.test:8000/v1"
    assert str(agent.model.client.base_url).rstrip("/") == "http://example.test:8000/v1"
    assert "base_url" not in agent.model.client_kwargs
    asyncio.run(agent.model.client.close())

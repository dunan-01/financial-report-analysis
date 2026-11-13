import asyncio
import json
from types import SimpleNamespace

import pytest

from orchestration.analysis_pipeline import AnalysisRequest, ParallelAnalysisPipeline, build_analysis_pipeline, run_single_skill
from orchestration.analysis_pipeline import NODES, REGISTRY
from orchestration.execution_context import input_prompt
from orchestration.progress_events import progress_scope

REQUEST = AnalysisRequest("DEMO001", "测试全称股份有限公司", "2025", "贷前尽调", "tester", user_materials={"text": "用户原始材料"})


def test_roots_overlap_join_waits_all_inputs_and_no_layer_barrier():
    async def check():
        started, done = set(), set()
        roots_started, unblock_unrelated = asyncio.Event(), asyncio.Event()
        def root(name):
            async def run(request):
                started.add(name)
                if len(started) == 3:
                    roots_started.set()
                await roots_started.wait()
                if name == "unrelated":
                    await unblock_unrelated.wait()
                done.add(name)
                return f"{name}-report", None
            return run
        async def join(request):
            assert {"a", "b"} <= done
            assert "unrelated" not in done  # 不能等待无关根节点结束后才启动下游
            assert request.upstream_results == {"a": "a-report", "b": "b-report"}
            assert request.user_materials == REQUEST.user_materials
            assert "a-report" in input_prompt() and "用户原始材料" in input_prompt()
            unblock_unrelated.set()
            return "joined-report", None
        pipeline = ParallelAnalysisPipeline()
        for name in ("a", "b", "unrelated"):
            pipeline.register(name, root(name))
        pipeline.register("join", join, upstream=("a", "b"))
        results = await asyncio.wait_for(pipeline(REQUEST), 3)
        assert results["join"][0] == "joined-report"
    asyncio.run(check())


@pytest.mark.parametrize("dependencies", [{"a": ("missing",)}, {"a": ("b",), "b": ("a",)}])
def test_invalid_graph_rejected_before_running(dependencies):
    calls = []
    async def branch(request):
        calls.append(True)
        return "report", None
    pipeline = ParallelAnalysisPipeline()
    for name, upstream in dependencies.items():
        pipeline.register(name, branch, upstream=upstream)
    with pytest.raises(ValueError):
        asyncio.run(pipeline(REQUEST))
    assert calls == []


def test_failed_upstream_never_runs_downstream():
    calls, events = [], []
    async def fail(request):
        raise ValueError("upstream failed")
    async def downstream(request):
        calls.append(True)
        return "report", None
    async def check():
        with progress_scope(sink=events.append):
            with pytest.raises(ValueError, match="upstream failed"):
                await ParallelAnalysisPipeline().register("root", fail).register("child", downstream, upstream=("root",))(REQUEST)
    asyncio.run(check())
    assert calls == []
    assert any(event["agent"] == "child" and event["status"] == "cancelled" for event in events)


def test_default_graph_has_exact_nine_nodes_and_expected_edges():
    pipeline = build_analysis_pipeline()
    assert set(pipeline._branches) == set(REGISTRY)
    assert pipeline._dependencies == {
        "industry": (), "policy": (), "risk_evidence": (), "verification": (),
        "due_diligence": ("industry",), "equity": ("due_diligence",),
        "financial": ("equity",), "related_party": ("financial",), "risk_extraction": ("financial",)}


def test_single_skill_passes_materials_without_adding_dependencies(monkeypatch):
    from orchestration import agent_factory
    seen = []
    async def fake(key, request, mode):
        seen.append((key, request))
        assert "用户原始材料" in input_prompt()
        return "single report", None
    monkeypatch.setattr(agent_factory, "run_registered_skill", fake)
    result = asyncio.run(run_single_skill("risk_extraction", REQUEST, mode="offline"))
    assert result[0] == "single report"
    assert len(seen) == 1 and seen[0][0] == "risk_extraction"
    assert seen[0][1].upstream_results == {}


@pytest.mark.parametrize("key", [node.key for node in NODES if node.key not in {"financial", "related_party"}])
def test_new_skill_agent_has_real_tool_and_dedicated_provider(monkeypatch, key):
    import data_provider
    from orchestration.agent_factory import build_skill_agent
    monkeypatch.setenv("LLM_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("LLM_MODEL", "test")
    monkeypatch.setenv("LLM_API_KEY", "not-a-real-key")
    provider = getattr(data_provider, REGISTRY[key].provider)
    calls = []
    def tracked(*args):
        calls.append(args)
        return provider(*args)
    monkeypatch.setattr(data_provider, REGISTRY[key].provider, tracked)
    agent, toolbox = build_skill_agent(key, REQUEST)
    async def check():
        tool = await agent.toolkit.get_tool("query_skill_data")
        assert tool is not None
        response = await tool.call()
        payload = json.loads(response.content[0].text)
        assert payload["company"]["company_name"] == REQUEST.company_name
        assert payload["data_source_availability"]["live_external_api"] is False
        await agent.model.client.close()
    asyncio.run(check())
    assert len(calls) == 1 and toolbox.audit_events


@pytest.mark.parametrize("key", ["financial", "related_party"])
def test_unified_factory_dispatches_specialized_agents(monkeypatch, key):
    from orchestration import agent_factory
    expected_agent, expected_toolbox = object(), object()
    if key == "financial":
        monkeypatch.setattr(agent_factory, "build_financial_agent", lambda *args: (expected_agent, expected_toolbox))
    else:
        monkeypatch.setattr(agent_factory, "build_related_party_agent", lambda *args: (expected_agent, expected_toolbox, {}))
    assert agent_factory.build_skill_agent(key, REQUEST) == (expected_agent, expected_toolbox)

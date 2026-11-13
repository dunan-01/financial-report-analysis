import asyncio

import pytest

from orchestration.analysis_pipeline import AnalysisRequest, ParallelAnalysisPipeline

REQUEST = AnalysisRequest("DEMO001", "示例企业", "2025", "贷前尽调", "tester")


def test_registered_agents_overlap_and_keep_named_results():
    async def check():
        started = set()
        both_started = asyncio.Event()

        def branch(name):
            async def run(request):
                assert request is REQUEST
                started.add(name)
                if len(started) == 3:
                    both_started.set()
                await asyncio.wait_for(both_started.wait(), timeout=1)
                return name, object()
            return run

        pipeline = ParallelAnalysisPipeline()
        for name in ("financial", "related_party", "future_agent"):
            pipeline.register(name, branch(name))
        results = await pipeline(REQUEST)
        assert list(results) == ["financial", "related_party", "future_agent"]
        assert [value[0] for value in results.values()] == list(results)

    asyncio.run(check())


def test_failed_branch_cancels_other_agents():
    async def check():
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def slow(request):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async def fail(request):
            await started.wait()
            raise ValueError("agent failed")

        pipeline = ParallelAnalysisPipeline().register("slow", slow).register("fail", fail)
        with pytest.raises(ValueError, match="agent failed"):
            await pipeline(REQUEST)
        assert cancelled.is_set()

    asyncio.run(check())


def test_reject_empty_duplicate_and_reserved_names():
    async def branch(request):
        return "report", None

    pipeline = ParallelAnalysisPipeline()
    for name in ("", "analysis_dispatcher"):
        with pytest.raises(ValueError):
            pipeline.register(name, branch)
    pipeline.register("financial", branch)
    with pytest.raises(ValueError):
        pipeline.register("financial", branch)
    with pytest.raises(ValueError, match="至少需要"):
        asyncio.run(ParallelAnalysisPipeline()(REQUEST))


def test_official_team_pipeline_is_used(monkeypatch):
    from agentscope.pipeline import TeamPipeline
    from orchestration import analysis_pipeline

    seen = []

    class ObservedPipeline(TeamPipeline):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            seen.append(self)

    monkeypatch.setattr(analysis_pipeline, "TeamPipeline", ObservedPipeline)

    async def branch(request):
        return "report", None

    results = asyncio.run(ParallelAnalysisPipeline().register("financial", branch)(REQUEST))
    assert results == {"financial": ("report", None)}
    assert len(seen) == 1

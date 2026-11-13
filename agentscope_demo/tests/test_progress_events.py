import asyncio

from orchestration.analysis_pipeline import AnalysisRequest, ParallelAnalysisPipeline
from orchestration.progress_events import emit, progress_scope
from orchestration.agent_factory import AutoAllowFunctionTool


def test_parallel_agents_keep_events_in_their_own_scope():
    events = []
    async def check():
        async def branch(request):
            emit("test", "running", request.company_id)
            await asyncio.sleep(.01)
            emit("test", "succeeded", request.company_id)
            return "report", None
        pipeline = ParallelAnalysisPipeline().register("first", branch).register("second", branch)
        with progress_scope(sink=events.append):
            await pipeline(AnalysisRequest("CUSTOM", "示例企业", "2025", "贷前尽调", "tester"))
        emit("outside", "running", "should not leak")
    asyncio.run(check())
    for name in ("first", "second"):
        statuses = [(e["step"], e["status"]) for e in events if e["agent"] == name]
        assert statuses == [("agent", "running"), ("test", "running"), ("test", "succeeded"), ("agent", "succeeded")]
    assert all(e["step"] != "outside" for e in events)


def test_broken_sink_does_not_change_tool_results():
    def broken(event):
        raise RuntimeError("browser disconnected")
    async def check():
        tool = AutoAllowFunctionTool(lambda: '{"status":"success"}')
        with progress_scope(sink=broken, agent="financial"):
            result = await tool.call()
        assert result.content[0].text == '{"status":"success"}'
    asyncio.run(check())


def test_tool_business_error_is_not_reported_as_success():
    events = []
    async def check():
        def query():
            return '{"status":"not_found"}'
        with progress_scope(sink=events.append):
            await AutoAllowFunctionTool(query).call()
    asyncio.run(check())
    assert [e["status"] for e in events if e["step"] == "tool:query"] == ["running", "failed"]

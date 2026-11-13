import asyncio
import json

from agentscope.message import ToolResultState
from agentscope.tool import ToolBase, ToolChunk
from orchestration.agent_factory import AutoAllowFunctionTool


def test_sync_and_async_json_tools_use_official_chunk_contract():
    def query(report_period: str = "") -> str:
        """查询年度数据。"""
        return json.dumps({"period": report_period})

    async def async_query(report_period: str = "") -> str:
        """异步查询年度数据。"""
        return query(report_period)

    for fn in (query, async_query):
        tool = AutoAllowFunctionTool(fn, is_concurrency_safe=False)
        assert isinstance(tool, ToolBase)
        assert tool.name == fn.__name__
        assert "report_period" in tool.input_schema["properties"]
        assert tool.is_concurrency_safe is False
        result = asyncio.run(tool.call(report_period="2025"))
        assert isinstance(result, ToolChunk)
        assert result.state == ToolResultState.SUCCESS
        assert json.loads(result.content[0].text) == {"period": "2025"}

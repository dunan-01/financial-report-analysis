"""通过 AgentScope 官方 TeamPipeline 执行依赖图中的并行与串行节点。

调度使用本地确定性模型适配器，确保每次完整运行全部分支，不额外请求 LLM。
并行执行、结果回传和取消清理由 TeamPipeline 负责。
本文件同时维护 SkillNode/NODES/REGISTRY 流程配置和管道执行逻辑。
"""
import asyncio
import time
import json
import hashlib
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Coroutine
from uuid import uuid4

from pydantic import SecretStr
from agentscope.agent import Agent
from agentscope.credential import OpenAICredential
from agentscope.message import AssistantMsg, Msg, TextBlock, ToolCallBlock, UserMsg
from agentscope.model import ChatResponse, OpenAIChatModel
from agentscope.pipeline import TeamMember, TeamPipeline
from agentscope.types import ReplyFinishedReason

from orchestration.execution_context import input_scope
from orchestration.progress_events import emit, progress_scope


# ── 流程配置：在此维护全部节点、数据接口及必需上游 ─────────────────
# 没有上游的节点同时启动；依赖就绪的下游立即启动，不等待无关节点。
@dataclass(frozen=True)
class SkillNode:
    key: str
    skill: str
    label: str
    provider: str
    upstream: tuple[str, ...] = ()


NODES = (
    SkillNode("industry", "credit-industry-analysis", "行业分析", "get_industry_data"),
    SkillNode("policy", "credit-policy-analysis", "信贷政策环境分析", "get_policy_data"),
    SkillNode("risk_evidence", "credit-risk-cot", "风险逆向思维链", "get_risk_evidence_data"),
    SkillNode("verification", "vlm-verifier", "反欺诈多模态交叉验证", "get_verification_data"),
    SkillNode("due_diligence", "credit-due-diligence", "尽调主引擎", "get_due_diligence_data", ("industry",)),
    SkillNode("equity", "equity-penetration-analysis", "股权穿透及关联方分析", "get_equity_data", ("due_diligence",)),
    SkillNode("financial", "financial-report-analysis", "财务报表深度分析", "get_financial_data", ("equity",)),
    SkillNode("related_party", "credit-related-party-detection", "关联交易识别与风险排查", "get_related_party_data", ("financial",)),
    SkillNode("risk_extraction", "credit-risk-extraction", "信贷风险结构化提取", "get_risk_extraction_data", ("financial",)),
)
REGISTRY = {node.key: node for node in NODES}


# ── 请求及管道执行 ─────────────────────────────────────────────

# 所有分支接收同一份业务请求；frozen=True 防止分支修改请求字段。
@dataclass(frozen=True)
class AnalysisRequest:
    company_id: str
    company_name: str
    report_period: str
    scenario: str
    operator: str
    user_materials: dict = field(default_factory=dict)
    upstream_results: dict[str, str] = field(default_factory=dict)


# 分支类型：接收 AnalysisRequest，返回协程；等待后得到 (报告文本, 工具箱)。
# Any 对应不同分支的工具箱类型，其中保存审计、模型名称等业务状态。
Branch = Callable[[AnalysisRequest], Coroutine[Any, Any, tuple[str, Any]]]


class _DispatchModel(OpenAIChatModel):
    """本地固定路由：首轮生成所有 TeamAssign，结果返回后结束。

    复用官方 Agent 消息/工具状态机；不调用父类的远程 API。
    """

    def __init__(self, names: list[str]):
        # 父类初始化满足官方模型接口；占位凭证没有远程服务用途。
        # 下面重写 __call__，所以调度不发送 OpenAI HTTP 请求。
        super().__init__(
            credential=OpenAICredential(api_key=SecretStr("local-dispatch-no-api")),
            model="local-team-dispatch", stream=False,
        )
        self.names = names
        # 每次管道调用都新建调度模型，因此此标记只属于当前一次分析。
        self.dispatched = False

    async def __call__(self, messages, tools=None, tool_choice=None, **kwargs):
        # 第二次被领导 Agent 调用时，成员结果已由官方管道回传，返回文本使循环结束。
        if self.dispatched:
            return ChatResponse(content=[TextBlock(text="全部分析任务已返回。")], is_last=True)
        # 首轮一次性返回全部 TeamAssign 工具调用；官方管道将不同成员并行运行。
        # 分批生成会改变并行范围，因此这里必须把所有已注册名称放在同一个响应中。
        self.dispatched = True
        return ChatResponse(content=[
            ToolCallBlock(
                id=uuid4().hex, name="TeamAssign",
                input=json.dumps({"member": name, "prompt": "执行本次已绑定的企业分析请求。"}, ensure_ascii=False),
            )
            for name in self.names
        ], is_last=True)


class _AnalysisMember(Agent):
    """将现有含校验/重试的 Agent 运行函数适配到官方成员事件接口。"""

    # 该对象是官方 TeamMember 持有的 Agent 外壳，真正的业务 Agent 由 branch 创建。
    def __init__(self, name: str, branch: Branch, request: AnalysisRequest, dependencies=(), completed=None, results=None):
        super().__init__(name=name, system_prompt="执行企业分析并校验报告。", model=_DispatchModel([]))
        self.dependencies = dependencies
        self.completed = completed or {}
        self.shared_results = results if results is not None else {}
        self.branch = branch
        self.request = request
        # 完整报告及工具箱留在本地，避免进入领导者上下文再次处理。
        self.result: tuple[str, Any] | None = None

    # 保持官方 Agent 事件接口签名；业务参数已绑定在 request 中，不从 inputs 重新解析。
    # 此适配器不转发业务 Agent 的中间事件，只在成功后提供最终完成消息。
    async def reply_stream(self, inputs=None, structured_schema=None, yield_final_msg=False):
        # await 包含取数、模型工具循环、报告校验和重试的完整分支。
        # 分支抛错时不伪造完成消息，异常交由官方管道传播并清理其他任务。
        with progress_scope(agent=self.name):
            if self.dependencies:
                emit("dependencies", "waiting", "等待所有必需上游完成", upstream=list(self.dependencies))
            try:
                for dependency in self.dependencies:
                    await self.completed[dependency].wait()
            except asyncio.CancelledError:
                emit("dependencies", "cancelled", "必需上游未能交付，当前节点不启动")
                emit("agent", "cancelled", "上游失败或调查中断，当前节点已取消")
                raise
            started = time.monotonic()
            request = self.request
            if self.dependencies:
                request = replace(request, upstream_results={key: self.shared_results[key][0] for key in self.dependencies})
                emit("dependencies", "succeeded", "必需上游全部完成，报告已传入", upstream=list(self.dependencies))
            emit("agent", "running", "智能体开始执行", input_refs={
                "user_materials_sha256": hashlib.sha256(json.dumps(request.user_materials, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
                "upstream": {name: {"characters": len(report), "sha256": hashlib.sha256(report.encode()).hexdigest()}
                             for name, report in request.upstream_results.items()},
            })
            try:
                with input_scope(request.user_materials, request.upstream_results):
                    self.result = await self.branch(request)
            except asyncio.CancelledError:
                emit("agent", "cancelled", "其他分支失败或调查中断，本分支已取消")
                raise
            except Exception:
                emit("agent", "failed", "智能体执行失败，请查看具体步骤或本地诊断")
                raise
            self.shared_results[self.name] = self.result
            if self.name in self.completed:
                self.completed[self.name].set()
            emit("agent", "succeeded", "智能体分析及报告校验完成", duration=round(time.monotonic() - started, 2))
        if yield_final_msg:
            # 报告保存在 result；领导者只需完成状态，避免再次传输整份报告。
            yield AssistantMsg(
                name=self.name, content="分析及报告校验完成。",
                finished_reason=ReplyFinishedReason.COMPLETED,
            )


class ParallelAnalysisPipeline:
    """官方 TeamPipeline 的业务入口；按名称注册并返回独立报告和审计对象。"""

    def __init__(self):
        self._branches: dict[str, Branch] = {}
        self._dependencies: dict[str, tuple[str, ...]] = {}

    # 注册只记录名称和运行函数，不启动任务；返回 self 支持链式注册。
    def register(self, name: str, branch: Branch, upstream=()):
        if not name or name == "analysis_dispatcher" or name in self._branches:
            raise ValueError(f"分支名称为空、保留或重复：{name!r}")
        self._branches[name] = branch
        self._dependencies[name] = tuple(upstream)
        return self

    async def __call__(self, request: AnalysisRequest) -> dict[str, tuple[str, Any]]:
        if not self._branches:
            raise ValueError("分析管道至少需要一个 Agent 分支")
        # 先检查缺失依赖和环；禁止在图不合法时启动任何 Agent。
        visited, visiting = set(), set()
        def visit(name):
            if name not in self._branches:
                raise ValueError(f"未注册的必需上游：{name}")
            if name in visiting:
                raise ValueError(f"依赖存在环：{name}")
            if name in visited:
                return
            visiting.add(name)
            for dependency in self._dependencies[name]:
                visit(dependency)
            visiting.remove(name)
            visited.add(name)
        for name in self._branches:
            visit(name)
        completed = {name: asyncio.Event() for name in self._branches}
        shared_results = {}
        members = [_AnalysisMember(name, branch, request, self._dependencies[name], completed, shared_results)
                   for name, branch in self._branches.items()]
        # 领导者使用固定路由模型，负责一次派发所有成员；它不生成财务判断。
        leader = Agent(
            name="analysis_dispatcher", system_prompt="同轮派发全部企业分析任务。",
            model=_DispatchModel(list(self._branches)),
        )
        # TeamMember 将成员 Agent 和能力说明交给官方 TeamPipeline。
        # 并行执行与结果回传在框架内部完成，本文件不再维护 asyncio 调度任务。
        pipeline = TeamPipeline(
            leader=leader,
            members=[TeamMember(agent=member, description=f"执行 {member.name} 分析") for member in members],
        )
        # reply_stream 是异步生成器，必须持续迭代才会推进派发、成员执行和汇合。
        # 当前入口不展示中间事件，所以这里只消费事件；完整报告从成员 result 获取。
        async for _ in pipeline.reply_stream(UserMsg(name="user", content="启动全部分析任务。")):
            pass
        # 全部成功才返回具名结果；检查 None 防止成员未执行就交付不完整结果。
        results = {}
        for member in members:
            if member.result is None:
                raise RuntimeError(f"分析成员未完成：{member.name}")
            results[member.name] = member.result
        return results


# 参数适配：财报分析需要报告期，关联交易运行函数当前没有报告期参数。
async def run_financial_agent(*args, **kwargs):
    from orchestration.agent_factory import run_financial_agent as run
    return await run(*args, **kwargs)


async def run_related_party_agent(*args, **kwargs):
    from orchestration.agent_factory import run_related_party_agent as run
    return await run(*args, **kwargs)


async def _financial(request: AnalysisRequest):
    return await run_financial_agent(
        request.company_id, request.company_name, request.report_period,
        request.scenario, request.operator,
    )


async def _related_party(request: AnalysisRequest):
    return await run_related_party_agent(
        request.company_id, request.company_name, request.scenario, request.operator,
    )


# 全部节点的统一注册入口：遍历本文件顶部的 NODES，不只注册财报和关联交易。
# 新增通用技能时扩展注册表与数据接口；需要专用工具/校验时再添加对应工厂。
def build_analysis_pipeline(mode="agentscope") -> ParallelAnalysisPipeline:
    from orchestration.agent_factory import run_registered_skill
    pipeline = ParallelAnalysisPipeline()
    for node in NODES:
        async def branch(request, key=node.key):
            if mode == "agentscope" and key == "financial":
                return await _financial(request)
            if mode == "agentscope" and key == "related_party":
                return await _related_party(request)
            return await run_registered_skill(key, request, mode=mode)
        pipeline.register(node.key, branch, upstream=node.upstream)
    return pipeline


async def run_single_skill(key, request, mode="agentscope"):
    """内部单技能入口：仅执行该技能，直接使用用户材料，不补跑任何上下游。"""
    pipeline = build_analysis_pipeline(mode)
    if key not in pipeline._branches:
        raise ValueError(f"未注册技能：{key}")
    return (await ParallelAnalysisPipeline().register(key, pipeline._branches[key])(replace(request, upstream_results={})))[key]

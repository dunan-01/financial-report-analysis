"""全部 9 个技能 Agent 的统一构建与运行入口。

财报和关联交易：使用专用工具箱及报告校验。
其余七个技能：共用配置式构建函数，各自创建独立 Agent、Toolkit 和上下文。
build_skill_agent(key, request)：统一构建入口，按 key 选择专用或通用构建实现。
run_registered_skill(key, request, mode)：统一运行入口，支持在线和离线。

analysis_pipeline.py 统一维护节点名称、数据接口、依赖和官方管道执行逻辑。
工厂只负责创建和运行一个技能，不自动启动上下游。
"""
import asyncio
import time
import json
import uuid
import hashlib
import inspect
import re
from functools import wraps
from typing import Any, Awaitable, Callable, Literal, Annotated
from pydantic import SecretStr, BaseModel, ConfigDict, Field, ValidationError, field_validator
from pathlib import Path
from types import SimpleNamespace

import data_provider
from orchestration.analysis_pipeline import REGISTRY

from agentscope.agent import Agent
from agentscope.credential import OpenAICredential
from agentscope.model import OpenAIChatModel
from agentscope.message import UserMsg, TextBlock, ToolResultState
from agentscope.tool import FunctionTool, Toolkit, ToolBase, ToolChunk
from agentscope.permission import PermissionDecision, PermissionBehavior

from orchestration.execution_context import input_prompt
from orchestration.model_settings import load_model_settings
from orchestration.progress_events import emit, TOOL_LABELS, get_skill_steps, summarize_source, redact_audit_text, diagnostic_directory
from data_provider import get_financial_data, get_company_info, get_financial_records
from data_provider import get_related_party_data
from tools.financial_tools import FinancialToolbox
from tools.related_party_tools import RelatedPartyToolbox
from tools.report_contract import (
    CLIENT_REPORT_RULES, validate_client_report, normalize_client_report, report_skeleton,
)
from tools.related_party_contract import validate_related_party_report, load_template as load_related_party_template
from tools.report_instructions import analysis_instructions, delivery_template
from tools.report_presentation import normalize_presentation, presentation_errors, PRESENTATION_RULES

BASE = Path(__file__).resolve().parents[1]
# 每次运行用独立 UUID 子目录保存原始回复与校验错误，便于并发任务分别追踪。
DIAGNOSTICS = BASE / "runs"

STEP_EXECUTION_RULES = """
【分阶段执行协议】
系统将按 Skill 的实际步骤逐轮发起任务。收到“当前只执行以下一个Skill步骤”时，
只执行该业务步骤，返回指定JSON状态、简明结果摘要与证据引用；不返回整份报告。
最终Markdown交付规则仅适用于汇总生成或修正最终报告，不适用于内部步骤结果。
步骤摘要只写可对外审阅的业务结果/缺口，不输出内部思考；不足以核验时明确资料不足。
"""



# 报告生成失败时携带诊断目录，供入口层记录；仍可按 ValueError 捕获。
class ReportValidationError(ValueError):
    diagnostic_dir: str = ""


# 连接“业务 Python 函数”和“AgentScope 工具协议”的适配器。
# 工具返回 JSON 字符串，模型收到的是包含这段字符串的 TextBlock。
class AutoAllowFunctionTool(FunctionTool):
    def __init__(self, func: Callable[..., str | Awaitable[str]], **kwargs: Any):
        # 业务工具保留 JSON 字符串契约，在 AgentScope 边界转换为官方 ToolChunk。
        # 保留原函数名称、文档及可追溯签名，供 FunctionTool 提取工具参数 Schema。
        @wraps(func)
        async def adapted(*args: Any, **call_kwargs: Any) -> ToolChunk:
            # 同步函数直接取返回值；异步函数返回可等待对象，需要 await 才能取到文本。
            step = "tool:" + func.__name__
            label = TOOL_LABELS.get(func.__name__, "执行业务工具")
            started = time.monotonic()
            emit(step, "running", label)
            try:
                result = func(*args, **call_kwargs)
                text = await result if inspect.isawaitable(result) else result
            except asyncio.CancelledError:
                emit(step, "cancelled", label + "已取消")
                raise
            except Exception:
                emit(step, "failed", label + "失败")
                raise
            try:
                business = json.loads(text)
            except (ValueError, TypeError):
                business = {}
            ok = not isinstance(business, dict) or business.get("status") not in {"error", "not_found", "failed", "no_data"}
            emit(step, "succeeded" if ok else "failed", label + ("完成" if ok else "返回异常，请核实数据"),
                 duration=round(time.monotonic() - started, 2), tool_name=func.__name__,
                 source_summary=summarize_source(business), tool_result_sha256=hashlib.sha256(text.encode()).hexdigest(), business_status=business.get("status", "returned") if isinstance(business, dict) else "returned")
            # SUCCESS 表示本次工具调用完成；JSON 内的业务 status 仍需模型读取判断。
            return ToolChunk(content=[TextBlock(text=text)], state=ToolResultState.SUCCESS)

        super().__init__(adapted, **kwargs)

    # 仅对这里注册的内部工具自动放行，避免每次取数都停下来等待确认。
    async def check_permissions(self, *_args: Any, **_kwargs: Any) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="内部工具，允许自动执行。",
        )


# ── 财报分析 Agent ─────────────────────────────────────────────────

# 将 Skill、计算规则和报告模板读入系统提示词；缺少必需文件时立即报错。
def _load_financial_instructions() -> str:
    files = [
        ("原始 Skill 指令", BASE.parent / "financial-report-analysis" / "SKILL.md"),
        ("财务指标公式参考", BASE.parent / "financial-report-analysis" / "references" / "financial-indicators-calculation.md"),
        ("现金流组合模式参考", BASE.parent / "financial-report-analysis" / "references" / "cashflow-pattern-guide.md"),
        ("财务粉饰识别参考", BASE.parent / "financial-report-analysis" / "references" / "fraud-detection-guide.md"),
        ("报告模板", BASE.parent / "financial-report-analysis" / "assets" / "financial-report-template.md"),
    ]
    sections = []
    for title, path in files:
        if path.exists():
            sections.append(f"## {title}\n{analysis_instructions(path.read_text(encoding='utf-8'))}")
        else:
            raise FileNotFoundError(f"报告生成所需文件不存在：{path}")
    return "\n\n".join(sections)


# 构建函数只组装 Agent 和工具，不启动模型分析。
def build_financial_agent(company_id: str, company_name: str = ""):
    settings = load_model_settings()
    # 先通过数据提供层获取当前企业数据，绑定到该企业专用的工具箱实例。
    # 当前提供层是 mock；模型随后调用的查询工具读取这份已取得的数据。
    payload = get_financial_data(company_id, company_name)
    company = get_company_info(payload)
    rows = get_financial_records(payload)
    toolbox = FinancialToolbox(company_id=company_id, company=company, rows=rows)
    toolbox.model_name = settings.model

    # 同一工具箱含审计、计算等可变状态，所以内部工具不标记为并发安全。
    # 这不影响不同 Agent 在 TeamPipeline 中并行，它们各自持有独立工具箱。
    tools: list[ToolBase] = [
        AutoAllowFunctionTool(toolbox.query_financial_data, is_concurrency_safe=False),
        AutoAllowFunctionTool(toolbox.calculate_financial_indicators, is_concurrency_safe=False),
        AutoAllowFunctionTool(toolbox.get_data_quality_summary, is_concurrency_safe=False),
    ]
    toolkit = Toolkit(tools=tools)

    # 系统提示词声明角色、工具使用顺序和业务约束；实际工具调用由 Agent 的模型循环驱动。
    # 这里的顺序是提示词要求，并非 Python 代码强制逐个调用工具。
    system_prompt = f"""
你是企业信贷财务分析 Agent。必须严格遵循下方 Skill 和参考资料。
本次任务 company_id 固定为：{company_id}，不得查询其他企业。

必须自主按顺序调用工具：
1. query_financial_data(report_period)：从信贷系统接口获取该企业所有已入库年度原始科目；
2. calculate_financial_indicators(report_period)：使用 Python 工具内置公式计算；
3. get_data_quality_summary(report_period)：检查字段覆盖、数据来源和口径限制；
4. 仅依据工具结果和 Skill 生成完整中文 Markdown 财报分析报告。

重要规则：
- 不得虚构数据库中没有的科目、行业均值、审计意见、附注、账龄或关联方拆分数据。
- 不可计算的指标保留模板原行，写"未提供/暂无法评价"，用业务语言简述缺少的资料。
- SY31 是归母净利润，不是扣非归母净利润；使用 SY30 回退时必须注明口径。
- DSCR 是近似口径；关联资金占用率不可用时不能用其他应收款总额代替。
- 金额标明人民币元；指标写清报告期和单位。公式、来源字段代码和取数过程仅供内部分析，不进入交付报告。
- 内部区分事实、计算结果和分析判断，交付正文使用客户经理易读的业务表述。
- 若数据少于3个年度，明确说明趋势分析受限。
- 严格按报告模板填写八个章节及全部编号子章节，不增加数据缺口等额外章节。
- 报告是分析辅助材料，不替代信贷审批。
- 指标名称必须严格按模板 financial-report-template.md 中的写法，不得添加括号备注或其他修饰。例如必须用"净利率"而非"净利率（归母口径）"，必须用"投入资本回报率"而非"投入资本回报率（ROIC）"。

{ _load_financial_instructions() }

{CLIENT_REPORT_RULES}
"""

    system_prompt += STEP_EXECUTION_RULES
    # 一个业务 Agent = 系统指令 + 远程模型客户端 + 可调用工具集合。
    agent = Agent(
        name="financial_report_analysis_agent",
        system_prompt=system_prompt,
        model=OpenAIChatModel(
            # SecretStr 隐藏凭证的常规字符串展示；地址、模型和开关来自统一配置。
            credential=OpenAICredential(
                api_key=SecretStr(settings.api_key),
                # AgentScope 已从 credential 转传 base_url，不能在 client_kwargs 重复提供。
                base_url=settings.base_url,
            ),
            model=toolbox.model_name,
            stream=settings.stream,
        ),
        toolkit=toolkit,
    )
    return agent, toolbox


async def run_financial_agent(company_id, company_name, report_period, scenario, operator):
    """运行财报分析 Agent。返回 (report_md, toolbox)。"""
    # 每次分析创建新 Agent，后续修正复用它的上下文和同一工具箱。
    emit("data", "running", "获取财务数据并准备分析工具")
    agent, toolbox = build_financial_agent(company_id, company_name)
    emit("data", "succeeded", "财务数据及分析工具已就绪", model=getattr(toolbox, "model_name", None))
    prompt = f"""
请对企业"{company_name}"（company_id={company_id}）开展财报分析。
报告期：{report_period}；分析场景：{scenario}；操作人：{operator}。
请先自主调用工具取数、计算、检查数据质量，再生成完整 Markdown 财报分析报告。
最终回答严格填写 financial-report-template.md 全部章节和表格。面向客户经理，不展示公式或技术来源；数据缺口只在对应模板位置简述。
"""
    prompt += input_prompt()
    toolbox.report_validation_events = []
    diagnostic_dir = diagnostic_directory("financial", DIAGNOSTICS)
    diagnostic_dir.mkdir(parents=True, exist_ok=True)
    toolbox.report_diagnostics = str(diagnostic_dir)
    await execute_skill_steps(agent, "financial", prompt, diagnostic_dir, toolbox)
    periods = sorted({str(row["report_period"]) for row in getattr(toolbox, "rows", [])
                      if str(row["report_period"]) <= str(report_period)})[-3:]
    prompt += "\n【现在开始最终交付，不返回步骤JSON】仅填写以下骨架，保留行列与标题。\n" + report_skeleton(periods or [str(report_period)])
    agent, prompt = prepare_report_writer(agent, "financial", toolbox, prompt, report_period=report_period)
    errors: list[str] = []
    # 最多三轮：初次生成一次，校验不通过再修正两次；不是三次重新创建 Agent。
    for attempt in range(3):
        # reply 内部完成模型推理与工具调用循环，最终返回本轮消息。
        emit("report", "running", "模型分析并生成报告" if attempt == 0 else f"模型正在第{attempt}次修正报告", attempt=attempt + 1)
        reply = await checked_reply(agent, UserMsg(name="user", content=prompt))
        emit("report", "succeeded", "本轮报告已生成，准备交付校验", attempt=attempt + 1)
        raw_report = reply.get_text_content() or ""
        # 先做交付格式归一化，再按报告契约检查；校验通过才交给调用方。
        emit("validation", "running", "检查报告章节、表格和交付规范")
        report, normalized = normalize_client_report(raw_report)
        errors = list(dict.fromkeys(validate_client_report(report) + presentation_errors(report, company_name)))
        event = {"attempt": attempt + 1, "normalized": normalized, "errors": errors}
        toolbox.report_validation_events.append(event)
        # 每轮都保存原文和错误；即使最终失败也能查看具体失败内容。
        prefix = diagnostic_dir / f"attempt_{attempt + 1}"
        prefix.with_suffix(".raw.md").write_text(raw_report, encoding="utf-8")
        prefix.with_suffix(".normalized.md").write_text(report, encoding="utf-8")
        prefix.with_suffix(".validation.json").write_text(json.dumps(event, ensure_ascii=False, indent=2), encoding="utf-8")
        if not errors:
            emit("validation", "succeeded", "报告交付校验通过", attempt=attempt + 1)
            return report, toolbox
        emit("validation", "retrying" if attempt < 2 else "failed",
             f"第{attempt + 1}轮报告校验未通过，共{len(errors)}项问题",
             attempt=attempt + 1, errors=errors[:5])
        # 修正提示词同时带上错误清单和模板骨架，要求模型返回完整报告。
        periods = sorted({str(row["report_period"]) for row in getattr(toolbox, "rows", [])
                          if str(row["report_period"]) <= str(report_period)})
        prompt = (
            "报告未通过交付校验，请基于已取得的数据修正并重新返回完整Markdown报告，不能只返回修改片段。\n"
            + "\n".join(f"- {error}" for error in errors)
            + "\n" + CLIENT_REPORT_RULES
            + "\n请逐项填写下面的模板骨架，保留所有表头、指标行和标题，不要重设计表格。叙述每项一至两句即可。\n"
            + report_skeleton(periods or [str(report_period)])
        )
    # 用尽修正次数仍不合格就抛出异常，由管道及入口层处理失败。
    error = ReportValidationError("报告经两次修正仍未通过模板校验（" + str(len(errors)) + "项）。"
                       "前几项：" + "；".join(errors[:3]) + f"。原始回复和完整错误已保存：{diagnostic_dir}")
    error.diagnostic_dir = str(diagnostic_dir)
    raise error


# ── 关联交易识别 Agent ────────────────────────────────────────────

# 加载关联交易 Skill、实际报告模板和统一交付规则；必需文件缺失时直接报错。
def _load_related_party_instructions() -> str:
    skill_path = BASE.parent / "credit-related-party-detection" / "SKILL.md"
    return analysis_instructions(skill_path.read_text(encoding="utf-8")) + "\n\n" + load_related_party_template() + "\n\n" + PRESENTATION_RULES


# 构建关联交易 Agent：数据来源和工具集不同，模型配置机制与财报 Agent 一致。
def build_related_party_agent(company_id: str, company_name: str = ""):
    settings = load_model_settings()
    # 聚合数据包括客户、股权图谱、关联方、流水、异常、红旗及数据源状态。
    payload = get_related_party_data(company_id, company_name)
    toolbox = RelatedPartyToolbox(company_id, payload)
    toolbox.model_name = settings.model

    # 同一工具箱含审计、计算等可变状态，所以内部工具不标记为并发安全。
    # 这不影响不同 Agent 在 TeamPipeline 中并行，它们各自持有独立工具箱。
    tools: list[ToolBase] = [
        AutoAllowFunctionTool(toolbox.get_customer_basic_info, is_concurrency_safe=False),
        AutoAllowFunctionTool(toolbox.get_equity_penetration, is_concurrency_safe=False),
        AutoAllowFunctionTool(toolbox.get_related_party_list, is_concurrency_safe=False),
        AutoAllowFunctionTool(toolbox.get_transaction_flows, is_concurrency_safe=False),
        AutoAllowFunctionTool(toolbox.get_data_source_status, is_concurrency_safe=False),
    ]
    toolkit = Toolkit(tools=tools)

    company_name = payload.get("company", {}).get("company_name", company_id)
    # 系统提示词声明角色、工具使用顺序和业务约束；实际工具调用由 Agent 的模型循环驱动。
    # 这里的顺序是提示词要求，并非 Python 代码强制逐个调用工具。
    system_prompt = f"""
你是企业信贷关联交易识别与关联风险排查 Agent。必须严格遵循下方 Skill。
本次任务 company_id 固定为：{company_id}。

必须自主按顺序调用工具：
1. get_customer_basic_info — 查询客户基本信息
2. get_equity_penetration — 查询股权穿透图谱
3. get_related_party_list — 查询六维关联方清单
4. get_transaction_flows — 查询交易流水
5. get_data_source_status — 查询数据源可用性（征信系统当前未接入，须在报告中注明）

重要规则：
- 严格按照 related-party-report-template.md 模板生成报告，保留全部章节。
- 征信接口当前未接入，在第七章明确标注"征信系统不可用"。
- 不虚构数据，不可用字段写"未提供"。
- 一票否决触发时在报告顶部红色标注。
- 交付正文使用客户经理易读的业务表述。

{_load_related_party_instructions()}
"""

    system_prompt += STEP_EXECUTION_RULES
    # 一个业务 Agent = 系统指令 + 远程模型客户端 + 可调用工具集合。
    agent = Agent(
        name="related_party_detection_agent",
        system_prompt=system_prompt,
        model=OpenAIChatModel(
            # SecretStr 隐藏凭证的常规字符串展示；地址、模型和开关来自统一配置。
            credential=OpenAICredential(
                api_key=SecretStr(settings.api_key),
                # AgentScope 已从 credential 转传 base_url，不能在 client_kwargs 重复提供。
                base_url=settings.base_url,
            ),
            model=toolbox.model_name,
            stream=settings.stream,
        ),
        toolkit=toolkit,
    )
    return agent, toolbox, payload


async def run_related_party_agent(company_id, company_name, scenario, operator):
    """运行关联交易识别 Agent。返回 (report_md, toolbox)。"""
    # 运行函数统一返回 (Markdown 报告, 工具箱)，便于管道汇合结果和入口记录审计。
    emit("data", "running", "获取关联交易数据并准备分析工具")
    agent, toolbox, payload = build_related_party_agent(company_id, company_name)
    emit("data", "succeeded", "关联交易数据及分析工具已就绪", model=getattr(toolbox, "model_name", None))
    prompt = f"""
请对企业"{company_name}"（company_id={company_id}）开展关联交易识别与关联风险排查。
排查场景：{scenario}；操作人：{operator}。
请先自主调用工具获取数据，再严格按照 related-party-report-template.md 模板生成完整 Markdown 报告。
征信系统当前未接入，须在报告中注明。
"""
    prompt += input_prompt()
    toolbox.report_validation_events = []
    diagnostic_dir = diagnostic_directory("related_party", DIAGNOSTICS)
    diagnostic_dir.mkdir(parents=True, exist_ok=True)
    await execute_skill_steps(agent, "related_party", prompt, diagnostic_dir, toolbox)
    prompt += "\n【现在开始最终交付，不返回步骤JSON】严格填写以下客户报告模板，不附内部审计。\n" + load_related_party_template()
    agent, prompt = prepare_report_writer(agent, "related_party", toolbox, prompt)
    errors: list[str] = []
    # 最多三轮：初次生成一次，校验不通过再修正两次；不是三次重新创建 Agent。
    for attempt in range(3):
        # reply 内部完成模型推理与工具调用循环，最终返回本轮消息。
        emit("report", "running", "模型分析并生成报告" if attempt == 0 else f"模型正在第{attempt}次修正报告", attempt=attempt + 1)
        reply = await checked_reply(agent, UserMsg(name="user", content=prompt))
        emit("report", "succeeded", "本轮报告已生成，准备交付校验", attempt=attempt + 1)
        raw_report = reply.get_text_content() or ""
        # 清理展示包装后校验；原始回复另存，风险正文不得删除。
        emit("validation", "running", "检查关联交易报告交付规范")
        report, normalized = normalize_presentation(raw_report)
        errors = list(dict.fromkeys(validate_related_party_report(report) + presentation_errors(report, company_name)))
        event = {"attempt": attempt + 1, "normalized": normalized, "errors": errors}
        toolbox.report_validation_events.append(event)
        # 每轮都保存原文和错误；即使最终失败也能查看具体失败内容。
        prefix = diagnostic_dir / f"rp_attempt_{attempt + 1}"
        prefix.with_suffix(".raw.md").write_text(raw_report, encoding="utf-8")
        prefix.with_suffix(".validation.json").write_text(json.dumps(event, ensure_ascii=False, indent=2), encoding="utf-8")
        prefix.with_suffix(".normalized.md").write_text(report, encoding="utf-8")
        if not errors:
            emit("validation", "succeeded", "报告交付校验通过", attempt=attempt + 1)
            return report, toolbox
        emit("validation", "retrying" if attempt < 2 else "failed",
             f"第{attempt + 1}轮报告校验未通过，共{len(errors)}项问题",
             attempt=attempt + 1, errors=errors[:5])
        prompt = (
            "报告未通过交付校验，请基于已取得的数据修正并重新返回完整Markdown报告。\n"
            + "\n".join(f"- {error}" for error in errors)
            + "\n" + PRESENTATION_RULES
        )
    # 用尽修正次数仍不合格就抛出异常，由管道及入口层处理失败。
    error = ReportValidationError("关联交易报告经两次修正仍未通过校验。")
    error.diagnostic_dir = str(diagnostic_dir)
    raise error


# ── 配置式技能工厂及全部技能离线预览 ───────────────────────────

def load_skill_instructions(key):
    directory = BASE.parent / REGISTRY[key].skill
    sections = [analysis_instructions((directory / "SKILL.md").read_text(encoding="utf-8"))]
    for subdir in ("assets", "references"):
        for path in sorted((directory / subdir).glob("*.md")):
            sections.append(f"\n## 参考文件：{path.name}\n" + analysis_instructions(path.read_text(encoding="utf-8")))
    return "\n".join(sections)


def build_skill_agent(key, request):
    """九个技能共用的构建入口；每次返回独立的 (Agent, toolbox)。"""
    if key == "financial":
        return build_financial_agent(request.company_id, request.company_name)
    if key == "related_party":
        agent, toolbox, _ = build_related_party_agent(request.company_id, request.company_name)
        return agent, toolbox
    node = REGISTRY[key]
    settings = load_model_settings()
    toolbox = SimpleNamespace(audit_events=[], model_name=settings.model, report_validation_events=[], payload=None)

    async def query_skill_data(unused: str = "") -> str:
        """调用本技能数据来源接口，取得企业材料、数据可用性和来源限制。

        Args:
            unused: 占位参数，无需传入。
        """
        provider = getattr(data_provider, node.provider)
        toolbox.payload = await asyncio.to_thread(provider, request.company_id, request.company_name)
        toolbox.audit_events.append({"tool": "query_skill_data", "status": "success", "provider": node.provider, "source_summary": summarize_source(toolbox.payload)})
        return json.dumps(toolbox.payload, ensure_ascii=False)

    tools: list[ToolBase] = [AutoAllowFunctionTool(query_skill_data, is_concurrency_safe=False)]
    agent = Agent(
        name=node.skill.replace("-", "_") + "_agent",
        system_prompt=(f"你负责{node.label}，只能分析本次企业。必须先调用 query_skill_data 获取本技能接口材料。"
            "所有外部业务数据当前是模拟数据，不得声称已查询实时政策、征信、图像或权威数据库。"
            "严格依照技能执行；资料缺失时交付缺口及影响说明，不猜测风险结论。"
            "风险逆向分析只交付简明的证据、计算结果和结论，不输出内部思考过程。"
            "不要执行技能中建议的其他技能，依赖由管道控制。"
            "使用Markdown交付本技能规定的章节或结构化字段，不使用代码围栏。\n"
            + load_skill_instructions(key) + "\n" + PRESENTATION_RULES + STEP_EXECUTION_RULES),
        model=OpenAIChatModel(credential=OpenAICredential(api_key=SecretStr(settings.api_key), base_url=settings.base_url),
                              model=settings.model, stream=settings.stream),
        toolkit=Toolkit(tools=tools),
    )
    return agent, toolbox


async def run_registered_skill(key, request, mode="agentscope"):
    node = REGISTRY[key]
    if mode == "offline":
        return await run_offline_skill(key, request)
    if key == "financial":
        return await run_financial_agent(request.company_id, request.company_name, request.report_period, request.scenario, request.operator)
    if key == "related_party":
        return await run_related_party_agent(request.company_id, request.company_name, request.scenario, request.operator)
    emit("data", "running", f"准备{node.label}的接口工具")
    agent, toolbox = build_skill_agent(key, request)
    emit("data", "succeeded", "技能及接口工具已注册", model=getattr(toolbox, "model_name", None))
    directory = diagnostic_directory(key, DIAGNOSTICS)
    directory.mkdir(parents=True, exist_ok=True)
    prompt = (f"请对{request.company_name}开展{node.label}。企业ID：{request.company_id}；"
              f"报告期：{request.report_period}；场景：{request.scenario}；操作人：{request.operator}。"
              "先调用工具取数，再仅返回完整报告。\n" + input_prompt())
    await execute_skill_steps(agent, key, prompt, directory, toolbox)
    directory_templates = sorted((BASE.parent / REGISTRY[key].skill / "assets").glob("*template*.md"))
    for path in directory_templates:
        if "disclaimer" not in path.name:
            prompt += "\n【最终交付模板；仅输出客户正文】\n" + delivery_template(path.read_text(encoding="utf-8"))
    agent, prompt = prepare_report_writer(agent, key, toolbox, prompt)
    for attempt in range(3):
        emit("report", "running", "生成本技能报告" if not attempt else f"第{attempt}次修正报告", attempt=attempt + 1)
        reply = await checked_reply(agent, UserMsg(name="user", content=prompt))
        raw = reply.get_text_content() or ""
        report, normalized = normalize_presentation(raw)
        emit("report", "succeeded", "本轮报告已生成")
        emit("validation", "running", "检查报告正文、来源和企业全称")
        errors = presentation_errors(report, request.company_name)
        if not report.startswith("#"):
            errors.append("报告须从Markdown标题开始。")
        if not toolbox.audit_events:
            errors.append("未调用本技能数据接口，须先调用 query_skill_data。")
        if "模拟" not in report and "虚构" not in report:
            errors.append("当前接口为模拟数据，报告须明确注明。")
        event = dict(attempt=attempt + 1, normalized=normalized, errors=errors)
        toolbox.report_validation_events.append(event)
        (directory / f"attempt_{attempt + 1}.raw.md").write_text(raw, encoding="utf-8")
        (directory / f"attempt_{attempt + 1}.normalized.md").write_text(report, encoding="utf-8")
        (directory / f"attempt_{attempt + 1}.validation.json").write_text(json.dumps(event, ensure_ascii=False), encoding="utf-8")
        if not errors:
            emit("validation", "succeeded", "技能报告交付校验通过")
            return report, toolbox
        emit("validation", "retrying" if attempt < 2 else "failed", "报告需要修正", errors=errors)
        prompt = "请重新返回完整报告，修正以下问题：\n" + "\n".join(errors) + PRESENTATION_RULES
    error = ReportValidationError(f"{node.label}报告校验未通过。诊断目录：{directory}")
    error.diagnostic_dir = str(directory)
    raise error


async def run_offline_skill(key, request):
    """每个节点均执行其接口/预览分支；不请求 LLM，不伪装真实分析。"""
    node = REGISTRY[key]
    emit("data", "running", "读取本技能模拟接口数据")
    payload = await asyncio.to_thread(getattr(data_provider, node.provider), request.company_id, request.company_name)
    toolbox = SimpleNamespace(audit_events=[{"tool": node.provider, "status": "success", "mode": "offline"}],
                              model_name=None, report_validation_events=[], calculated={})
    emit("data", "succeeded", "模拟接口数据已返回", source_summary=summarize_source(payload))
    for step in get_skill_steps(key):
        emit(step["id"], "skipped", step["label"], summary="离线预览不执行模型业务分析", execution_mode="offline")
    emit("report", "running", "生成本节点离线预览")
    if key == "financial":
        from tools.indicator_calculator import calculate_all
        from tools.client_preview import offline_report
        toolbox.calculated = calculate_all(payload["records"])
        report = offline_report(payload["company"], request.report_period, toolbox.calculated, request.scenario, request.operator)
    elif key == "related_party":
        from run_demo import _offline_related_party_report
        report = _offline_related_party_report(payload, request.scenario, request.operator)
    else:
        report = (f"# {request.company_name} — {node.label}\n\n"
                  "> 本报告为模拟数据离线预览，未调用业务模型，不能视为正式调查结论。\n\n"
                  f"## 一、调查信息\n\n企业：{request.company_name}\n\n报告期：{request.report_period}；场景：{request.scenario}\n\n"
                  "## 二、材料覆盖\n\n" + "\n".join(f"- {name}：已提供模拟材料" if value else f"- {name}：未提供"
                      for name, value in payload["materials"].items())
                  + "\n\n## 三、上游输入\n\n" + ("\n".join(f"- {REGISTRY[name].label if name in REGISTRY else name}：报告已接收（{len(text)}字）"
                      for name, text in request.upstream_results.items()) or "无必需上游，直接使用本次用户材料及模拟接口。")
                  + "\n\n## 四、限制与待核实事项\n\n仅验证流程依赖与材料传递，风险结论待正式分析；征信未接入。"
                  + ("多模态原始图片未提供，未进行视觉核验。" if key == "verification" else ""))
    emit("report", "succeeded", "本节点离线预览已生成")
    return report, toolbox


async def execute_skill_steps(agent, key, task_prompt, directory, toolbox):
    """按 Skill 顺序真正分步调用模型；只审计简明结果，不采集内部思考。

    不从最终报告倒推步骤完成，也不依时间自动打勾。最终报告校验失败只修正报告，
    不重跑已完成的业务步骤。阶段结果是模型提供的业务摘要，不是独立核验事实。
    """
    toolbox.skill_step_events = []
    for step in get_skill_steps(key):
        started = time.monotonic()
        emit(step["id"], "running", step["label"], execution_mode="online")
        try:
            result, parse_method = {}, ""
            for attempt in range(2):
                prompt = (task_prompt + "\n当前只执行以下一个Skill步骤，必要时调用已注册工具；"
                    "不要提前生成最终报告。仅返回一个JSON对象："
                    '{"status":"completed或insufficient_data或not_applicable",'
                    '"summary":"不超过200字的业务结果或资料缺口",'
                    '"evidence_refs":["来源工具名、用户材料或上游报告名称"]}。'
                    "只给结论摘要和证据引用，不给内部推理或思考过程；缺少图片/征信/市场数据时标明资料不足，不能认定已核验通过。"
                    "本步骤完成后等待下一步骤指令。\n" + step["label"] + "\n" + step["instructions"])
                if attempt:
                    prompt += "\n上次步骤结果格式不符合要求，请只返回上述JSON对象。"
                reply = await checked_reply(agent, UserMsg(name="user", content=prompt), structured=True)
                raw = reply.get_text_content() or ""
                native = getattr(reply, "structured_output", None)
                if native is not None:
                    (directory / f"{step['id'].replace(':', '_')}_{attempt + 1}.structured.json").write_text(json.dumps(native, ensure_ascii=False, default=str), encoding="utf-8")
                (directory / f"{step['id'].replace(':', '_')}_{attempt + 1}.raw.md").write_text(raw, encoding="utf-8")
                try:
                    result, parse_method = parse_step_result(raw, getattr(reply, "structured_output", None))
                    break
                except (ValueError, TypeError) as exc:
                    reason = str(exc)
                    if attempt:
                        error = ReportValidationError(f"{REGISTRY[key].label} {step['label']}：模型未提供有效的步骤结果摘要（{reason}）。诊断：{directory}")
                        error.diagnostic_dir = str(directory)
                        raise error from None
                    emit(step["id"], "retrying", step["label"] + "：步骤格式需要修正", errors=[reason], attempt=attempt + 1)
            status = {"completed": "succeeded", "insufficient_data": "limited", "not_applicable": "skipped"}[result["status"]]
            event = dict(step=step["id"], status=status, message=step["label"],
                         summary=redact_audit_text(result["summary"]), evidence_refs=[redact_audit_text(ref) for ref in result.get("evidence_refs", [])],
                         summary_origin="model_reported", parse_method=parse_method, artifact_ref=str((directory / f"{step['id'].replace(':', '_')}.result.json")), duration=round(time.monotonic() - started, 2))
            toolbox.skill_step_events.append(event)
            (directory / f"{step['id'].replace(':', '_')}.result.json").write_text(json.dumps(event, ensure_ascii=False, indent=2), encoding="utf-8")
            emit(**event)
        except asyncio.CancelledError:
            emit(step["id"], "cancelled", step["label"] + "已取消")
            raise
        except Exception as exc:
            emit(step["id"], "failed", step["label"] + "执行失败", errors=[redact_audit_text(str(exc))[:500]], failure_type=type(exc).__name__)
            raise



def prepare_report_writer(agent, key, toolbox, prompt, *, report_period=None):
    """阶段分析与最终写作分离，防止前序JSON响应/工具过程污染客户报告。"""
    # 小型替身保留原有回复接口，便于单独验证交付校验逻辑。
    if not all(hasattr(agent, name) for name in ("model", "toolkit", "_system_prompt")):
        return agent, prompt
    rules = CLIENT_REPORT_RULES if key == "financial" else PRESENTATION_RULES
    writer = Agent(name=REGISTRY[key].skill.replace("-", "_") + "_report_writer",
        system_prompt=("你是最终客户报告撰写Agent。只输出一份完整中文Markdown报告。"
            "业务分析步骤已经执行，下面的JSON仅为参考材料，不是要求你输出JSON。"
            "不要输出审计日志、代码围栏、Mermaid、ASCII图、内部工具名或技术字段。"
            "股权和关联关系使用Markdown表格，警示用引用加粗；不要用HTML控制颜色。"
            "不要抄写示例或虚构缺失事实。优先按本次用户消息附带的具体模板填写。\n" + rules),
        model=agent.model, toolkit=agent.toolkit)
    snapshot = {"step_results": getattr(toolbox, "skill_step_events", []),
                "source_data": getattr(toolbox, "payload", None),
                "company": getattr(toolbox, "company", None),
                "records": getattr(toolbox, "rows", None),
                "calculated": getattr(toolbox, "calculated", None)}
    if key == "financial" and report_period is not None:
        rows = snapshot["records"] or []
        years = sorted({str(row["report_period"]) for row in rows if str(row["report_period"]) <= str(report_period)})[-3:]
        snapshot["records"] = [row for row in rows if str(row["report_period"]) in years]
        if isinstance(snapshot["calculated"], dict):
            snapshot["calculated"] = {year: value for year, value in snapshot["calculated"].items() if str(year) in years}
        snapshot["delivery_years"] = years
    prompt += "\n【分析资料，只供撰写；内部字段不得出现在正文】\n" + json.dumps(snapshot, ensure_ascii=False, default=str)
    return writer, prompt


class SkillStepResult(BaseModel):
    """步骤状态是业务结果；资料不足是有效状态，不是程序异常。"""
    model_config = ConfigDict(extra="forbid", strict=True)
    status: Literal["completed", "insufficient_data", "not_applicable"]
    summary: str = Field(min_length=1, max_length=500)
    evidence_refs: list[Annotated[str, Field(max_length=200)]] = Field(default_factory=list, max_length=20)

    @field_validator("summary")
    @classmethod
    def nonblank_summary(cls, value):
        if not value.strip():
            raise ValueError("摘要不能为空")
        return value


def parse_step_result(raw, structured_output=None):
    """优先取官方结构化输出；兼容说明文字/围栏和可恢复JSON语法，不改业务状态。"""
    def validate(value):
        try:
            return SkillStepResult.model_validate(value).model_dump()
        except ValidationError as exc:
            details = "; ".join(".".join(str(item) for item in error["loc"]) + ":" + error["type"] for error in exc.errors())
            raise ValueError("步骤字段校验失败：" + details) from None
    if structured_output is not None:
        return validate(structured_output), "native_structured"
    text = raw.strip()
    blocks = re.findall(r"```(?:json)?[ \t]*\n(.*?)\n```", text, re.S | re.I)
    if len(blocks) > 1:
        raise ValueError("存在多个步骤JSON，无法确定唯一结果")
    candidate = blocks[0].strip() if blocks else text
    begin, end = candidate.find("{"), candidate.rfind("}")
    if begin < 0 or end < begin:
        raise ValueError("回复没有步骤JSON对象")
    candidate = candidate[begin:end + 1]
    try:
        data = json.loads(candidate)
        method = "json" if candidate == text else "extracted_json"
    except json.JSONDecodeError:
        # AgentScope 已依赖此解析库；只修复语法，再用严格Schema检查字段和业务状态。
        from json_repair import repair_json
        data = repair_json(candidate, return_objects=True)
        method = "json_repaired"
    return validate(data), method


async def checked_reply(agent, message, *, structured=False):
    kwargs = {}
    if structured and "structured_schema" in inspect.signature(agent.reply).parameters:
        kwargs["structured_schema"] = SkillStepResult
    reply = await agent.reply(message, **kwargs)
    task = asyncio.current_task()
    reason = getattr(reply, "finished_reason", None)
    # 某些Agent路径会捕获CancelledError并返回“interruption”消息；不能将它当作格式失败重试。
    if (task and task.cancelling()) or reason == "interrupted":
        raise asyncio.CancelledError()
    if reason in {"error", "exceed_max_iters"}:
        raise RuntimeError(f"模型调用未正常完成（{reason}），未进入步骤格式校验")
    return reply

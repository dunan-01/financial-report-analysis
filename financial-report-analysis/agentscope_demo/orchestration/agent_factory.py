"""AgentScope 2.x Agent 构建与运行入口。

2.x 关键变化：
- 使用 Agent，而不是 1.x 的 ReActAgent；
- 使用 OpenAICredential + OpenAIChatModel 连接企业OpenAI兼容接口；
- 使用 FunctionTool 包装 Python 函数，再通过 Toolkit(tools=[...]) 注册；
- 使用 UserMsg 和 await agent.reply(...)，不再使用 Msg + agent(...).
"""
import json
import uuid
from pathlib import Path

from agentscope.agent import Agent
from agentscope.credential import OpenAICredential
from agentscope.model import OpenAIChatModel
from agentscope.message import UserMsg

from agentscope.tool import FunctionTool, Toolkit
from orchestration.model_settings import load_model_settings

from tools.financial_tools import FinancialToolbox
from tools.report_contract import CLIENT_REPORT_RULES, validate_client_report, normalize_client_report, report_skeleton

BASE = Path(__file__).resolve().parents[1]
DIAGNOSTICS = BASE / "audit" / "diagnostics"

from agentscope.permission import (
    PermissionDecision,
    PermissionBehavior,
)


class AutoAllowFunctionTool(FunctionTool):
    """内部可信工具：自动允许执行，不逐次请求人工授权。"""
# 这个类本身不查询数据库，也不计算指标；它只决定工具是否可以执行。
    async def check_permissions(self, tool_input, context):
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="财报分析内部工具，允许自动执行。",
        )
    
def _load_instructions() -> str:
    """读取当前财报 Skill、公式、现金流、舞弊识别及报告模板。返回字符串。`-> str` 是返回类型说明"""
    files = [
        ("原始 Skill 指令", BASE.parent / "SKILL.md"),
        ("财务指标公式参考", BASE.parent / "references" / "financial-indicators-calculation.md"),
        ("现金流组合模式参考", BASE.parent / "references" / "cashflow-pattern-guide.md"),
        ("财务粉饰识别参考", BASE.parent / "references" / "fraud-detection-guide.md"),
        ("报告模板", BASE.parent / "assets" / "financial-report-template.md"),
    ]
    sections = []
    for title, path in files:
        if path.exists():
            sections.append(f"## {title}\n{path.read_text(encoding='utf-8')}")
        else:
            raise FileNotFoundError(f"报告生成所需文件不存在：{path}")
    return "\n\n".join(sections)


def build_agent(company_id: str):
    """构建绑定单一企业上下文的财报分析 Agent。"""
    settings = load_model_settings()

    toolbox = FinancialToolbox(company_id)
    # 保存实际传给模型的配置，供运行审计使用。
    toolbox.model_name = settings.model

    # AgentScope 2.x：FunctionTool 将普通 Python 函数封装为模型可调用工具。
    toolkit = Toolkit(
        tools=[
                    AutoAllowFunctionTool(
                        toolbox.query_financial_data, is_concurrency_safe=False
                    ),
                    # 把函数封装为模型工具。框架会从函数中提取：
                    # - 函数名，作为工具名称。
                    # - 文档字符串，作为工具说明。
                    # - 参数名和类型，作为调用参数规范。      
                    AutoAllowFunctionTool(
                        toolbox.calculate_financial_indicators, is_concurrency_safe=False
                    ),
                    AutoAllowFunctionTool(
                        toolbox.get_data_quality_summary, is_concurrency_safe=False
                    ),
            ],
    )

    system_prompt = f"""
你是企业信贷财务分析 Agent。必须严格遵循下方 Skill 和参考资料。
本次任务 company_id 固定为：{company_id}，不得查询其他企业。

必须自主按顺序调用工具：
1. query_financial_data(report_period)：从 MySQL 获取该企业所有已入库年度原始科目；
2. calculate_financial_indicators(report_period)：使用 Python 工具内置公式计算；
3. get_data_quality_summary(report_period)：检查字段覆盖、数据来源和口径限制；
4. 仅依据工具结果和 Skill 生成完整中文 Markdown 财报分析报告。

重要规则：
- 不得虚构数据库中没有的科目、行业均值、审计意见、附注、账龄或关联方拆分数据。
- 不可计算的指标保留模板原行，写“未提供/暂无法评价”，用业务语言简述缺少的资料。
- SY31 是归母净利润，不是扣非归母净利润；使用 SY30 回退时必须注明口径。
- DSCR 是近似口径；关联资金占用率不可用时不能用其他应收款总额代替。
- 金额标明人民币元；指标写清报告期和单位。公式、来源字段代码和取数过程仅供内部分析，不进入交付报告。
- 内部区分事实、计算结果和分析判断，交付正文使用客户经理易读的业务表述。
- 若数据少于3个年度，明确说明趋势分析受限。
- 严格按报告模板填写八个章节及全部编号子章节，不增加数据缺口等额外章节。
- 报告是分析辅助材料，不替代信贷审批。

{ _load_instructions() }

{CLIENT_REPORT_RULES}
"""


    agent = Agent(
        
        name="financial_report_analysis_agent",
        system_prompt=system_prompt,
        model=OpenAIChatModel(
            credential=OpenAICredential(api_key=settings.api_key),
            model=toolbox.model_name,
            client_kwargs={"base_url": settings.base_url},
            stream=settings.stream,
        ),
        toolkit=toolkit,
    )
    return agent, toolbox

# 定义异步分析入口。
async def run_agent(company_id, 
                    company_name, 
                    report_period, 
                    scenario, 
                    operator):
    """调用 AgentScope 2.x Agent，返回报告正文及工具审计事件。"""
    agent, toolbox = build_agent(company_id)
    prompt = f"""
请对企业“{company_name}”（company_id={company_id}）开展财报分析。
报告期：{report_period}；分析场景：{scenario}；操作人：{operator}。
请先自主调用工具取数、计算、检查数据质量，再生成完整 Markdown 财报分析报告。
最终回答严格填写 financial-report-template.md 全部章节和表格。面向客户经理，不展示公式或技术来源；数据缺口只在对应模板位置简述。
"""
    toolbox.report_validation_events = []
    diagnostic_dir = DIAGNOSTICS / uuid.uuid4().hex
    diagnostic_dir.mkdir(parents=True, exist_ok=True)
    toolbox.report_diagnostics = str(diagnostic_dir)
    for attempt in range(3):
        reply = await agent.reply(UserMsg(name="user", content=prompt))
        raw_report = reply.get_text_content() or ""
        report, normalized = normalize_client_report(raw_report)
        errors = validate_client_report(report)
        event = {"attempt": attempt + 1, "normalized": normalized, "errors": errors}
        toolbox.report_validation_events.append(event)
        prefix = diagnostic_dir / f"attempt_{attempt + 1}"
        prefix.with_suffix(".raw.md").write_text(raw_report, encoding="utf-8")
        prefix.with_suffix(".normalized.md").write_text(report, encoding="utf-8")
        prefix.with_suffix(".validation.json").write_text(json.dumps(event, ensure_ascii=False, indent=2), encoding="utf-8")
        if not errors:
            return report, toolbox
        periods = sorted({str(row["report_period"]) for row in getattr(toolbox, "rows", [])
                          if str(row["report_period"]) <= str(report_period)})
        prompt = (
            "报告未通过交付校验，请基于已取得的数据修正并重新返回完整Markdown报告，不能只返回修改片段。\n"
            + "\n".join(f"- {error}" for error in errors)
            + "\n" + CLIENT_REPORT_RULES
            + "\n请逐项填写下面的模板骨架，保留所有表头、指标行和标题，不要重设计表格。叙述每项一至两句即可。\n"
            + report_skeleton(periods or [str(report_period)])
        )
    error = ValueError("报告经两次修正仍未通过模板校验（" + str(len(errors)) + "项）。"
                       "前几项：" + "；".join(errors[:3]) + f"。原始回复和完整错误已保存：{diagnostic_dir}")
    error.diagnostic_dir = str(diagnostic_dir)
    raise error

"""独立运行入口：财报分析 + 关联交易识别，合并 HTML 输出。"""
import argparse, asyncio, json, uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv
from data_provider import get_financial_data, get_company_info, get_financial_records
from tools.indicator_calculator import calculate_all
from tools.report_renderer import render_report, render_combined_html, render_investigation_html
from orchestration.analysis_pipeline import NODES
from tools.report_contract import require_client_report
from tools.client_preview import offline_report
from orchestration.progress_events import emit, progress_scope, storage_scope, AuditTrail, redact_audit_text
from orchestration.analysis_pipeline import AnalysisRequest, build_analysis_pipeline

BASE=Path(__file__).resolve().parent
RUNS=BASE/"runs"
TZ=timezone(timedelta(hours=8))
def now(): return datetime.now(TZ)
async def main():
    load_dotenv()
    ap=argparse.ArgumentParser()
    ap.add_argument("--mode",choices=["agentscope","offline"],default="agentscope")
    ap.add_argument("--company-id",required=True)
    ap.add_argument("--company-name",default="")
    ap.add_argument("--report-period",required=True)
    ap.add_argument("--scenario",default="贷前尽调")
    ap.add_argument("--operator",default="DEMO_OPERATOR")
    args=ap.parse_args()
    await run_analysis(args)


async def run_analysis(args, *, output_dir: Path | None = None, on_event=None, audit_trail=None):
    """CLI/网页共用同一任务目录与唯一审计文件；网页只订阅已持久化事件。"""
    run_id = Path(output_dir).name if output_dir is not None else uuid.uuid4().hex
    root = Path(output_dir) if output_dir is not None else RUNS / run_id
    delegated = audit_trail is not None
    trail = audit_trail or AuditTrail(root, args, run_id)
    def notify(event):
        event = trail.append(event)
        if on_event:
            on_event(event)
    with storage_scope(root), progress_scope(sink=notify):
        try:
            result = await _run_analysis(args, output_dir=root)
        except Exception as exc:
            summary = {"run_id": run_id, "status": "failed", "mode": args.mode,
                       "company_id": args.company_id, "report_period": args.report_period,
                       "error": redact_audit_text(str(exc)), "diagnostics": "diagnostics/", "audit": "audit.jsonl"}
            (root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            if not delegated:
                emit("job", "failed", "调查失败，请查看当前任务诊断记录")
            raise
        if not delegated:
            emit("job", "succeeded", "调查完成")
        return result


async def _run_analysis(args, *, output_dir: Path):
    output_root = Path(output_dir)
    reports_dir = output_root / "reports"
    run_id = output_root.name
    started=now()
    emit("request", "running", "核对企业和报告期")
    payload=get_financial_data(args.company_id, args.company_name)
    company=get_company_info(payload)
    if not company:
        raise RuntimeError(f"未找到企业ID={args.company_id}")
    rows=get_financial_records(payload)
    if not rows:
        raise RuntimeError("没有财务原始数据")
    periods=sorted({str(r["report_period"]) for r in rows})
    if args.report_period not in periods:
        raise RuntimeError(f"报告期{args.report_period}不存在，可用报告期：{periods}")
    emit("request", "succeeded", "企业和报告期核对完成")
    reports_dir.mkdir(parents=True, exist_ok=True)
    try:
        request = AnalysisRequest(
            company_id=args.company_id, company_name=args.company_name or company["company_name"],
            report_period=args.report_period, scenario=args.scenario, operator=args.operator,
            user_materials=getattr(args, "user_materials", {}),
        )
        results = await build_analysis_pipeline(args.mode)(request)
        report_fin, toolbox = results["financial"]
        report_rp, rp_toolbox = results["related_party"]
        require_client_report(report_fin)
        model_name = toolbox.model_name

        emit("output", "running", "保存报告并生成合并页面")
        stamp=now().strftime("%Y%m%d_%H%M%S")
        company_name=args.company_name or company["company_name"]

        # 保留财报和关联交易的兼容路径；下面统一保存全部九个节点。
        report_fin_path=reports_dir/f"{args.company_id}_{args.report_period}_{stamp}_financial.md"
        report_rp_path=reports_dir/f"{args.company_id}_{args.report_period}_{stamp}_related_party.md"

        # 合并 HTML，Tab 切换展示全部节点报告
        html_path=reports_dir/f"{args.company_id}_{args.report_period}_{stamp}.html"
        report_paths = {}
        for node in NODES:
            path = reports_dir / f"{args.company_id}_{args.report_period}_{stamp}_{node.key}.md"
            path.write_text(results[node.key][0], encoding="utf-8")
            report_paths[node.key] = path
        combined_html=render_investigation_html(
            reports={node.key: (node.label, results[node.key][0]) for node in NODES},
            company_name=company_name, report_period=args.report_period,
            scenario=args.scenario, generated_at=now().strftime("%Y-%m-%d %H:%M"),
        )
        html_path.write_text(combined_html, encoding="utf-8")

        audit={
            "run_id":run_id,"skill_name":"full-investigation-workflow",
            "skill_version":"2.0.0","mode":args.mode,"model":model_name,"company_id":args.company_id,
            "company_name":company_name,
            "report_period":args.report_period,"analysis_scenario":args.scenario,
            "operator":args.operator,"started_at":started.isoformat(),"finished_at":now().isoformat(),
            "input_data":{"source_systems":sorted({r["source_system"] for r in rows}),
                          "record_count":len(rows),"periods":periods},
            "status": "succeeded", "audit": "audit.jsonl", "diagnostics": "diagnostics/",
            "financial_report_path":str(report_fin_path.relative_to(output_root)),
            "related_party_report_path":str(report_rp_path.relative_to(output_root)),
            "html_report_path":str(html_path.relative_to(output_root)),
            "agents": {node.key: {"skill": node.skill, "required_upstream": list(node.upstream),
                "report_path": str(report_paths[node.key].relative_to(output_root)),
                "status": "succeeded",
                "tool_call_count": len(results[node.key][1].audit_events),
                "report_attempts": len(results[node.key][1].report_validation_events)} for node in NODES},
            "warnings":[
                "本 Demo 数据为 Mock 虚构数据。",
                "征信系统当前未接入。",
            ]
        }
        audit_path=output_root/"summary.json"
        audit_path.write_text(json.dumps(audit,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
        print(f"财报分析报告：{report_fin_path}")
        print(f"关联交易报告：{report_rp_path}")
        for node in NODES:
            if node.key not in {"financial", "related_party"}:
                print(f"{node.label}报告：{report_paths[node.key]}")
        print(f"合并 HTML 页面：{html_path}")
        print(f"任务汇总：{audit_path}")
        print(f"过程审计：{output_root / 'audit.jsonl'}")
        emit("output", "succeeded", "全部技能报告、合并页面和审计记录已保存")
        return {
            "financial": report_fin_path,
            "related_party": report_rp_path,
            "html": html_path,
            "summary": audit_path,
            "audit": output_root / "audit.jsonl",
            **report_paths,
        }
    except Exception:
        # 失败汇总及终态统一由 run_analysis 包装层写入，包括入参/取数阶段的失败。
        raise


def _offline_related_party_report(payload, scenario, operator) -> str:
    """离线模式生成关联交易报告预览。"""
    from tools.related_party_tools import RelatedPartyToolbox
    toolbox = RelatedPartyToolbox(payload.get("company", {}).get("company_id", ""), payload)
    rp = toolbox.related_parties
    ds = toolbox.data_source_availability
    lines = [f"# 关联方风险排查报告 — {payload.get('company', {}).get('company_name', '')}"]
    lines.append(f"\n> **报告编号**: RP-OFFLINE-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
    lines.append(f"> **排查时点**: {scenario}")
    lines.append(f"> **分析人**: {operator}")
    lines.append("\n### 一、报告基本信息")
    lines.append("| 字段 | 内容 |\n|------|------|")
    lines.append(f"| 客户全称 | {payload.get('company', {}).get('company_name', '')} |")
    lines.append(f"| 排查时点 | {scenario} |")
    lines.append(f"| 数据源 | {', '.join(k for k, v in ds.items() if v) + '（征信系统未接入）' if not ds.get('credit_report_api') else ''} |")
    lines.append("\n### 二、关联方清单")
    lines.append("| 序号 | 关联方名称 | 关联关系类型 | 穿透深度 | 关联占比 | 数据来源 | 可信度 |\n|------|-----------|-------------|---------|---------|---------|--------|")
    for i, p in enumerate(rp, 1):
        lines.append(f"| {i} | {p['name']} | {p['relation_type']} | {p['depth']} | {p['ratio']} | {p['source']} | {p['confidence']} |")
    lines.append("\n### 三、关联交易异常特征")
    lines.append("由 Agent 在运行模式下分析（离线预览跳过）")
    lines.append("\n### 四、关联风险评估")
    lines.append("待综合评估（离线预览）")
    lines.append("\n### 五、风险分级与处置建议")
    lines.append("待综合评估（离线预览）")
    lines.append("\n### 六、一票否决触发情况")
    lines.append("由 Agent 在运行模式下分析（离线预览跳过）")
    lines.append("\n### 七、数据缺失与降级处理")
    lines.append("| 数据源 | 是否可用 |\n|--------|---------|")
    lines.append(f"| 股权穿透接口 | {'是' if ds.get('equity_api') else '否'} |")
    lines.append(f"| 征信系统 | 否（当前未接入） |")
    lines.append(f"| 交易流水 | {'是' if ds.get('transaction_api') else '否'} |")
    lines.append(f"| 工商数据 | {'是' if ds.get('business_data_api') else '否'} |")
    return "\n".join(lines) + "\n"


if __name__=="__main__":
    asyncio.run(main())

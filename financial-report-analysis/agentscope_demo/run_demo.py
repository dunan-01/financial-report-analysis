"""独立运行入口：AgentScope 模式或离线链路验证模式。"""
import argparse, asyncio, json, uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv
from db.mysql_client import fetch_company, fetch_financial_values, record_analysis_run
from tools.indicator_calculator import calculate_all
from tools.report_renderer import render_report
from tools.report_contract import require_client_report
from tools.client_preview import offline_report
from orchestration.agent_factory import run_agent

BASE=Path(__file__).resolve().parent
REPORTS=BASE/"reports"; AUDIT=BASE/"audit"
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
    run_id=uuid.uuid4().hex
    started=now()
    company=fetch_company(args.company_id)
    if not company:
        raise RuntimeError(f"MySQL中未找到企业ID={args.company_id}，请先运行 seed_mock_data.py")
    rows=fetch_financial_values(args.company_id,None)
    if not rows:
        raise RuntimeError("MySQL中没有财务原始数据")
    periods=sorted({str(r["report_period"]) for r in rows})
    if args.report_period not in periods:
        raise RuntimeError(f"报告期{args.report_period}不存在，可用报告期：{periods}")
    AUDIT.mkdir(exist_ok=True); REPORTS.mkdir(exist_ok=True)
    try:
        if args.mode=="agentscope":
            report, toolbox=await run_agent(args.company_id,args.company_name or company["company_name"],
                                             args.report_period,args.scenario,args.operator)
            tool_events=toolbox.audit_events
            model_name=toolbox.model_name
            calculated=toolbox.calculated
            validation_events=toolbox.report_validation_events
        else:
            calculated=calculate_all(rows)
            report=offline_report(company,args.report_period,calculated,args.scenario,args.operator)
            tool_events=[
                {"tool":"query_financial_data","status":"success","row_count":len(rows),"company_id":args.company_id},
                {"tool":"calculate_financial_indicators","status":"success","periods":list(calculated)}
            ]
            model_name=None
            validation_events=[]
        require_client_report(report)
        stamp=now().strftime("%Y%m%d_%H%M%S")
        report_path=REPORTS/f"{args.company_id}_{args.report_period}_{stamp}.md"
        report_path.write_text(report,encoding="utf-8")
        html_path=report_path.with_suffix(".html")
        html_path.write_text(render_report(report, metadata={
            "报告期": args.report_period,
            "分析场景": args.scenario,
            "生成时间": now().strftime("%Y-%m-%d %H:%M"),
        }), encoding="utf-8")
        audit={
            "run_id":run_id,"skill_name":"financial-report-analysis","skill_version":"2.0.0",
            "mode":args.mode,"model":model_name,"company_id":args.company_id,
            "company_name":args.company_name or company["company_name"],
            "report_period":args.report_period,"analysis_scenario":args.scenario,
            "operator":args.operator,"started_at":started.isoformat(),"finished_at":now().isoformat(),
            "input_data":{"source_systems":sorted({r["source_system"] for r in rows}),
                          "record_count":len(rows),"periods":periods,
                          "source_record_ids":sorted({r["source_record_id"] for r in rows})},
            "tool_events":tool_events,
            "report_validation_events":validation_events,
            "report_diagnostics":getattr(toolbox, "report_diagnostics", None) if args.mode == "agentscope" else None,
            "calculation_results":calculated,
            "source_financial_records":rows,
            "calculation_file":"tools/indicator_calculator.py",
            "mapping_file":"config/financial_indicator_mapping.json",
            "mapping_file_role":"reference_only",
            "report_path":str(report_path.relative_to(BASE)),
            "html_report_path":str(html_path.relative_to(BASE)),
            "warnings":[
                "本 Demo 数据为 Mock 虚构数据。",
                "行业基准、审计意见、附注等未在映射字段中提供。",
                "ROE当前无法严格采用扣非归母净利润口径。",
                "DSCR为近似口径；关联资金占用率缺少关联方明细。"
            ]
        }
        audit_path=AUDIT/f"{args.company_id}_{stamp}_audit.json"
        audit_path.write_text(json.dumps(audit,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
        record_analysis_run(run_id,args.company_id,args.report_period,"completed",started.replace(tzinfo=None),
                            now().replace(tzinfo=None),str(report_path),str(audit_path))
        print(f"财报分析完成：{report_path}")
        print(f"HTML页面：{html_path}")
        print(f"审计记录：{audit_path}")
        print(report[:1500])
    except Exception as exc:
        failed_audit = AUDIT / f"{run_id}_failed.json"
        failed_audit.write_text(json.dumps({
            "run_id": run_id, "company_id": args.company_id,
            "report_period": args.report_period, "status": "failed",
            "error": str(exc), "report_diagnostics": getattr(exc, "diagnostic_dir", None),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        record_analysis_run(run_id,args.company_id,args.report_period,"failed",started.replace(tzinfo=None),
                            now().replace(tzinfo=None),audit_path=str(failed_audit),error_message=str(exc))
        raise

if __name__=="__main__":
    asyncio.run(main())

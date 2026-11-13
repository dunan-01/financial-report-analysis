"""AgentScope Toolkit：原始财务数据查询（经由 data_provider 获取）、指标计算、数据质量检查。"""
import json
import asyncio
import re
from tools.indicator_calculator import calculate_all

class FinancialToolbox:
    def __init__(self, company_id: str, company: dict, rows: list):
        self.company_id=company_id
        self.rows=rows
        self.calculated={}
        self.company=company
        self.audit_events=[]
        self.model_name: str = ""
        self.report_validation_events: list[dict] = []
        self.report_diagnostics: str = ""

    @staticmethod
    def _response(obj):
        """FunctionTool 可直接把字符串结果交还给 Agent。"""
        return json.dumps(obj, ensure_ascii=False, default=str)

    def _start_period_event(self, tool, report_period):
        """统一年度参数，容忍模型多传一层引号；其余非法格式显式报错。"""
        event = {"tool": tool, "report_period": report_period, "status": "started"}
        self.audit_events.append(event)
        if not isinstance(report_period, (str, int)) or isinstance(report_period, bool):
            period = None
        else:
            period = str(report_period).strip()
            while len(period) >= 2 and period[0] == period[-1] and period[0] in "\"'":
                period = period[1:-1].strip()
        if period is None or (period and not re.fullmatch(r"[0-9]{4}", period)):
            event["status"] = "error"
            return None, event, self._response({
                "status": "error", "message": "report_period 必须是四位年度（如2025），或留空查询全部年度。"
            })
        if period != report_period:
            event["raw_report_period"] = report_period
        event["report_period"] = period
        return period, event, None

    def _check_period_exists(self, period, event):
        available = sorted({str(r["report_period"]) for r in self.rows})
        if period and period not in available:
            event.update({"status": "not_found", "periods": [], "available_periods": available})
            return self._response({
                "status": "not_found", "report_period": period,
                "available_periods": available, "message": f"查询结果中没有报告期 {period}"
            })
        return None

    async def query_financial_data(self, report_period: str = "") -> str:
        """从 MySQL 查询企业指定年度的财务原始科目。必须先调用此工具，再计算指标。

        Args:
            report_period (str): 目标报告期，例如 2025；工具同时查询该企业历史年度，支持趋势分析。
        """
        report_period, event, error = self._start_period_event("query_financial_data", report_period)
        if error:
            return error
        self.calculated = {}
        if not self.company:
            event["status"]="not_found"
            return self._response({"status":"not_found","company_id":self.company_id,
                                   "message":"未找到该企业信息，请确认企业ID。"})
        if not self.rows:
            event["status"]="no_data"
            return self._response({"status":"no_data","company_id":self.company_id,
                                   "report_period":report_period,"message":"没有财务原始科目数据。"})
        self.calculated={}
        error = self._check_period_exists(report_period, event)
        if error:
            return error
        event.update({"status":"success","row_count":len(self.rows),
                                      "periods":sorted({str(x["report_period"]) for x in self.rows})})
        return self._response({
            "status":"success","company":self.company,
            "record_count":len(self.rows),
            "periods":sorted({str(x["report_period"]) for x in self.rows}),
            "raw_fields":[{"report_period":str(x["report_period"]),"field_code":x["field_code"],
                           "field_name":x["field_name"],"amount":float(x["amount"]) if x["amount"] is not None else None,
                           "source_system":x["source_system"],"source_record_id":x["source_record_id"],
                           "is_audited":bool(x["is_audited"])} for x in self.rows]
        })

    async def calculate_financial_indicators(self, report_period: str = "") -> str:
        """使用 indicator_calculator.py 内置公式计算财务指标。必须在 query_financial_data 之后调用。

        Args:
            report_period (str): 目标年度，例如 2025；留空时计算查询结果中的全部年度。
        """
        report_period, event, error = self._start_period_event("calculate_financial_indicators", report_period)
        if error:
            return error
        if not self.rows:
            event["status"] = "error"
            return self._response({"status":"error","message":"尚未查询原始数据，请先调用 query_financial_data。"})
        error = self._check_period_exists(report_period, event)
        if error:
            return error
        self.calculated=calculate_all(self.rows)
        event.update({"status":"success","periods":list(self.calculated)})
        # 返回该企业全部年度；目标报告期及其历史年度由 Agent 在分析时选取。
        selected=self.calculated
        if not selected:
            return self._response({"status":"not_found","message":f"查询结果中没有报告期 {report_period}"})
        return self._response({"status":"success","indicators":selected})

    async def get_data_quality_summary(self, report_period: str = "") -> str:
        """检查数据覆盖情况、来源可追溯性和 Excel 中明确列出的口径限制。

        Args:
            report_period (str): 年度报告期，例如 2025；留空表示全部年度。
        """
        report_period, event, error = self._start_period_event("get_data_quality_summary", report_period)
        if error:
            return error
        if not self.rows:
            event["status"] = "error"
            return self._response({"status":"error","message":"请先查询财务原始数据。"})
        error = self._check_period_exists(report_period, event)
        if error:
            return error
        rows=[r for r in self.rows if not report_period or str(r["report_period"])==report_period]
        periods=sorted({str(r["report_period"]) for r in rows})
        byp={}
        for p in periods:
            rs=[r for r in rows if str(r["report_period"])==p]
            codes={r["field_code"] for r in rs}
            required={"SY02","SY07","SY16","SY17","SY17_1","SY18","SY28","SY30","ZCFZ38","ZCFZ69","ZCFZ79","XJ25"}
            byp[p]={"record_count":len(rs),"available_codes":sorted(codes),
                    "missing_core_codes":sorted(required-codes),
                    "audited":all(bool(r["is_audited"]) for r in rs)}
        limitations=[
            "行业均值/行业基准字段未在本次Excel映射中提供，不能进行定量同业对标。",
            "审计意见、报表附注、前五大客户/供应商、账龄和商誉减值假设未在本次字段映射中提供。",
            "SY31为归母净利润，不是扣非归母净利润；SY31缺失时回退SY30并标注口径。",
            "ROIC未扣除受限货币资金，需补受限资金明细。",
            "DSCR使用XJ32资本开支总额近似维持性资本开支，ANNEX07期间口径需确认。",
            "关联资金占用率缺少关联方其他应收款拆分，状态应为不可计算。",
            "应收/存货周转天数按365天制；流动比率和速动比率以倍数输出。"
        ]
        event.update({"status":"success","periods":periods})
        return self._response({"status":"success","period_quality":byp,"limitations":limitations,
                               "source_systems":sorted({r["source_system"] for r in rows}),
                               "note":"所有数据为数据库返回原始字段；缺失数据不推测填充。"})

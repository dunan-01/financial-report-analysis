"""模拟信贷系统及关联交易相关数据接口。"""
import argparse, json, random
from pathlib import Path

# 仅使用 Excel 映射表中出现的字段代码；金额为虚构演示数据。
FIELD_NAMES = {
"SY02":"营业收入","SY07":"营业成本","SY16":"销售费用","SY17":"管理费用",
"SY17_1":"研发费用","SY18":"财务费用","SY18_1":"其中：利息费用",
"SY28":"利润总额","SY29":"所得税费用","SY30":"净利润","SY31":"归属于母公司所有者的净利润",
"ZCFZ01":"货币资金","ZCFZ06":"应收账款","ZCFZ13":"其他应收款","ZCFZ15":"存货",
"ZCFZ18":"流动资产合计","ZCFZ38":"资产合计","ZCFZ39":"短期借款",
"ZCFZ60":"流动负债合计","ZCFZ61":"长期借款","ZCFZ62":"应付债券",
"ZCFZ62_12":"租赁负债","ZCFZ69":"负债合计","ZCFZ79":"所有者权益合计",
"XJ01":"销售商品、提供劳务收到的现金","XJ25":"经营活动产生的现金流量净额",
"XJ32":"购建固定资产、无形资产和其他长期资产支付的现金",
"CF34":"固定资产折旧","CF35":"无形资产摊销","CF36":"长期待摊费用摊销",
"ANNEX07":"偿还债务本息付现","ANNEX08":"上年存货","ANNEX09":"上年应收账款",
"ANNEX12":"上年总资产","ANNEX13":"上年所有者权益"
}
STATEMENT = {c: ("资产负债表" if c.startswith("ZCFZ") else
                 "利润表" if c.startswith("SY") else
                 "现金流量表" if c.startswith("XJ") else
                 "现金流量补充资料" if c.startswith("CF") else "补充数据")
             for c in FIELD_NAMES}
def build_payload(company_id="DEMO001", company_name="示例制造股份有限公司"):
    rng = random.Random(20261109)
    rows_by_year={}
    for year, scale in [(2022,0.82),(2023,0.90),(2024,0.96),(2025,1.0)]:
        revenue=100_000_000*scale
        cost=revenue*(0.70 + (2025-year)*0.002)
        selling=revenue*0.045; admin=revenue*0.055; rd=revenue*0.025; finance=3_000_000*scale
        interest=2_200_000*scale
        profit_total=revenue-cost-selling-admin-rd-finance+2_000_000*scale
        tax=max(profit_total,0)*0.20
        net=profit_total-tax
        assets=150_000_000*scale
        equity=65_000_000*scale
        liabilities=assets-equity
        current_assets=80_000_000*scale
        current_liab=48_000_000*scale
        inventory=18_000_000*scale
        receivables=24_000_000*scale
        cash=20_000_000*scale
        short_debt=20_000_000*scale
        long_debt=25_000_000*scale
        values={
            "SY02":revenue,"SY07":cost,"SY16":selling,"SY17":admin,"SY17_1":rd,
            "SY18":finance,"SY18_1":interest,"SY28":profit_total,"SY29":tax,
            "SY30":net,"SY31":net*0.94,
            "ZCFZ01":cash,"ZCFZ06":receivables,"ZCFZ13":3_000_000*scale,
            "ZCFZ15":inventory,"ZCFZ18":current_assets,"ZCFZ38":assets,
            "ZCFZ39":short_debt,"ZCFZ60":current_liab,"ZCFZ61":long_debt,
            "ZCFZ62":2_000_000*scale,"ZCFZ62_12":1_000_000*scale,
            "ZCFZ69":liabilities,"ZCFZ79":equity,
            "XJ01":revenue*0.93,"XJ25":net*1.08,
            "XJ32":8_000_000*scale,"CF34":4_500_000*scale,
            "CF35":500_000*scale,"CF36":200_000*scale,
            "ANNEX07":10_000_000*scale
        }
        # ANNEX values是上年期初辅助字段；2022年没有2021数据，保持为空不造数。
        if year > 2022:
            prev=rows_by_year[year-1]
            values.update({"ANNEX08":prev["ZCFZ15"],"ANNEX09":prev["ZCFZ06"],
                           "ANNEX12":prev["ZCFZ38"],"ANNEX13":prev["ZCFZ79"]})
        rows_by_year[year]=values
    records=[]
    for year, values in rows_by_year.items():
        for code, amount in values.items():
            records.append({
                "company_id":company_id,"report_period":str(year),
                "statement_type":STATEMENT[code],"field_code":code,
                "field_name":FIELD_NAMES[code],"amount":round(amount,2) if amount is not None else None,
                "currency":"CNY","source_system":"MOCK_CREDIT_SYSTEM_API",
                "source_record_id":f"MOCK-{company_id}-{year}-{code}",
                "is_audited":1 if year<2025 else 0
            })
    return {"company":{"company_id":company_id,"company_name":company_name,"industry_name":"通用设备制造业"},
            "source_system":"MOCK_CREDIT_SYSTEM_API","records":records}

# ── 关联交易相关 Mock 数据 ──────────────────────────────────────────

RELATED_PARTIES = [
    {"name":"示例华远钢铁贸易有限公司","relation_type":"股权关联","depth":"直接层","ratio":"35%","source":"工商数据","confidence":"高"},
    {"name":"李四(自然人)","relation_type":"股权关联","depth":"穿透2层","ratio":"25%","source":"股权穿透","confidence":"高"},
    {"name":"示例恒通贸易有限公司","relation_type":"管理层关联","depth":"-","ratio":"-","source":"工商高管查询","confidence":"高"},
    {"name":"王五(实控人配偶)","relation_type":"家族关联","depth":"推定层","ratio":"-","source":"关系图谱","confidence":"中"},
    {"name":"示例嘉诚设备销售有限公司","relation_type":"交易关联","depth":"-","ratio":"35%","source":"企业财报","confidence":"高"},
    {"name":"示例安信融资担保有限公司","relation_type":"担保关联","depth":"-","ratio":"-","source":"征信报告","confidence":"高"},
    {"name":"示例同创供应链管理有限公司","relation_type":"隐性关联","depth":"-","ratio":"-","source":"工商比对","confidence":"中"},
]

def build_related_party_payload(company_id="DEMO001", company_name="示例制造股份有限公司"):
    """模拟关联交易接口返回数据。"""
    rng = random.Random(20261109)
    # 交易流水 mock
    transactions = []
    for i in range(8):
        amt = round(rng.uniform(500000, 5000000), 2)
        transactions.append({
            "from_company": company_name if i % 2 == 0 else RELATED_PARTIES[i % len(RELATED_PARTIES)]["name"],
            "to_company": RELATED_PARTIES[i % len(RELATED_PARTIES)]["name"] if i % 2 == 0 else company_name,
            "amount": amt,
            "date": f"2025-{rng.randint(1,12):02d}-{rng.randint(1,28):02d}",
            "purpose": "货款" if i < 5 else "借款",
            "has_contract": i < 6,
            "has_invoice": i < 6,
            "has_logistics": i < 4,
            "source_system": "MOCK_CORE_BANK_API",
        })
    return {
        "company": {"company_id": company_id, "company_name": company_name, "credit_code": "91110000MA001XXXX"},
        "customer_info": {
            "company_id": company_id,
            "company_name": company_name,
            "industry": "通用设备制造业",
            "registered_capital": 50000000,
            "legal_person": "张三",
            "address": "XX省XX市XX区XX路XX号",
            "contact_phone": "010-XXXXXXXX",
        },
        "equity_graph": {
            "nodes": [
                {"id": company_id, "name": company_name, "type": "借款企业"},
                {"id": "SH001", "name": "张三(实控人)", "type": "自然人", "share": "60%"},
                {"id": "SH002", "name": "李四(自然人)", "type": "自然人", "share": "25%"},
                {"id": "COM001", "name": "示例华远钢铁贸易有限公司", "type": "法人", "share": "35%"},
                {"id": "COM002", "name": "示例景盛投资有限公司", "type": "法人", "share": "15%"},
            ],
            "edges": [
                {"from": "SH001", "to": company_id, "relation": "持股60%"},
                {"from": "SH002", "to": company_id, "relation": "持股25%"},
                {"from": "COM001", "to": company_id, "relation": "持股35%"},
                {"from": "COM002", "to": company_id, "relation": "持股15%"},
                {"from": "SH001", "to": "COM001", "relation": "实控人"},
            ],
        },
        "related_parties": RELATED_PARTIES,
        "transactions": transactions,
        "data_source_availability": {
            "equity_api": True, "credit_report_api": False,
            "transaction_api": True, "business_data_api": True,
        },
        "source_system": "MOCK_RELATED_PARTY_API",
    }

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--company-id",default="DEMO001")
    ap.add_argument("--company-name",default="示例制造股份有限公司")
    ap.add_argument("--print-only",action="store_true")
    args=ap.parse_args()
    payload=build_payload(args.company_id,args.company_name)
    if args.print_only:
        print(json.dumps(payload,ensure_ascii=False,indent=2))
    else:
        out=Path("mock_api_payload.json")
        out.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
        print(f"Mock接口payload已生成：{out.resolve()}，记录数={len(payload['records'])}")


def build_skill_payload(skill_key, company_id, company_name=""):
    """各技能的独立 mock 接口响应；不伪造真实联网、征信或图像识别成功。"""
    company_name = company_name or "示例制造股份有限公司"
    finance = build_payload(company_id, company_name)
    related = build_related_party_payload(company_id, company_name)
    materials = {
        "industry": {"industry": "通用设备制造业", "market_observations": [
            {"item": "模拟行业营业收入增速", "value": "5%", "period": "2025", "source": "虚构行业测试样本"}],
            "industry_benchmark": None},
        "policy": {"policy_documents": [{"title": "模拟制造业授信政策样例", "content": "关注现金流覆盖与担保独立性。仅为测试材料，不代表真实监管要求。"}], "live_policy_search": False},
        "risk_evidence": {"target_risks": ["短期偿债压力", "客户集中度"], "financial_records": finance["records"], "evidence_limitations": ["未取得行业对标数据"]},
        "verification": {"images": [], "documents": [], "structured_transactions": related["transactions"], "limitation": "未提供图片或原始材料，无法进行实际视觉核验；仅能分析模拟结构化记录，不能认定材料真实或虚假。"},
        "due_diligence": {"customer_info": related["customer_info"], "financial_records": finance["records"], "site_visit": None, "credit_report": None},
        "equity": {"equity_graph": related["equity_graph"], "related_parties": related["related_parties"], "credit_report": None},
        "risk_extraction": {"customer_info": related["customer_info"], "source_documents": [], "limitation": "使用用户材料及上游财报分析进行风险提取，不编造未披露的风险证据。"},
    }
    return {"company": finance["company"], "source_system": "MOCK_" + skill_key.upper() + "_API",
            "as_of": "2025-12-31", "materials": materials[skill_key],
            "data_source_availability": {"mock_interface": True, "live_external_api": False, "credit_report_api": False}}

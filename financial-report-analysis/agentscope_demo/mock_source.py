"""模拟信贷系统财务数据接口：生成带系统字段代码的原始科目数据，不生成财务比率。"""
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

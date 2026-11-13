"""指标计算器：财务指标实际计算公式的唯一执行来源。字段映射JSON仅作参考。"""
from collections import defaultdict

def _num(v):
    try:
        if v is None: return None
        return float(v)
    except (TypeError, ValueError):
        return None

def _div(a,b):
    a,b=_num(a),_num(b)
    if a is None or b in (None,0): return None
    return a/b

def calculate_all(rows):
    """输入 DB 原始记录，输出各报告期指标、来源字段及不可计算原因。"""
    by_period=defaultdict(dict)
    meta={}
    for r in rows:
        period=str(r["report_period"])
        by_period[period][r["field_code"]]=_num(r["amount"])
        meta[(period,r["field_code"])]={
            "field_name":r["field_name"],"source_system":r["source_system"],
            "source_record_id":r["source_record_id"],"is_audited":bool(r["is_audited"])
        }
    periods=sorted(by_period)
    results={}
    def put(out,name,value,formula,codes,unit="比例",note=None):
        missing=[c for c in codes if out.get(c) is None]
        out["指标"][name]={
            "value":round(value,6) if value is not None else None,
            "unit":unit,"formula":formula,
            "source_field_codes":codes,
            "status":"available" if value is not None else "unavailable",
            "missing_fields":missing,
            "note":note
        }
    for p in periods:
        d=by_period[p]
        out={"report_period":p,"raw_fields":d,"指标":{}}
        rev,cost=d.get("SY02"),d.get("SY07")
        core=None
        if all(d.get(c) is not None for c in ["SY02","SY07","SY16","SY17","SY17_1","SY18"]):
            core=rev-cost-d["SY16"]-d["SY17"]-d["SY17_1"]-d["SY18"]
        net=d.get("SY31") if d.get("SY31") is not None else d.get("SY30")
        net_note="采用归母净利润 SY31" if d.get("SY31") is not None else "SY31缺失，回退净利润SY30；口径已降级"
        put(out,"毛利率",_div(rev-cost,rev) if rev is not None and cost is not None else None,
            "(SY02-SY07)/SY02",["SY02","SY07"])
        put(out,"核心利润率",_div(core,rev) if core is not None else None,
            "(SY02-SY07-SY16-SY17-SY17_1-SY18)/SY02",
            ["SY02","SY07","SY16","SY17","SY17_1","SY18"],
            note="按Python工具内置公式；任一组成科目缺失则不计算，不自动当作0")
        put(out,"净利率",_div(net,rev),"(SY31优先，否则SY30)/SY02",
            ["SY31或SY30","SY02"],note=net_note)
        # prior-year average balance items are provided as ANNEX codes in Excel.
        roe_profit=net
        roe_note=net_note+"；映射表未提供扣非归母净利润字段，因此不是扣非ROE"
        avg_equity=None
        if d.get("ANNEX13") is not None and d.get("ZCFZ79") is not None:
            avg_equity=(d["ANNEX13"]+d["ZCFZ79"])/2
        put(out,"净资产收益率（ROE）",_div(roe_profit,avg_equity),
            "SY31/SY30 ÷ ((ANNEX13+ZCFZ79)/2)",
            ["SY31或SY30","ANNEX13","ZCFZ79"],note=roe_note)
        avg_assets=None
        if d.get("ANNEX12") is not None and d.get("ZCFZ38") is not None:
            avg_assets=(d["ANNEX12"]+d["ZCFZ38"])/2
        put(out,"总资产收益率（ROA）",_div(net,avg_assets),
            "(SY30或SY31)/((ANNEX12+ZCFZ38)/2)",
            ["SY30或SY31","ANNEX12","ZCFZ38"],note=net_note)
        ebit=(d.get("SY28")+d.get("SY18_1")) if d.get("SY28") is not None and d.get("SY18_1") is not None else None
        tax_rate=_div(d.get("SY29"),d.get("SY28"))
        nopat=ebit*(1-tax_rate) if ebit is not None and tax_rate is not None and d.get("SY28",0)>0 else None
        debt_codes=["ZCFZ39","ZCFZ61","ZCFZ62","ZCFZ62_12"]
        interest_debt=sum(d[c] for c in debt_codes) if all(d.get(c) is not None for c in debt_codes) else None
        invested=(interest_debt+d["ZCFZ79"]-d["ZCFZ01"]) if interest_debt is not None and all(d.get(c) is not None for c in ["ZCFZ79","ZCFZ01"]) else None
        put(out,"投入资本回报率（ROIC）",_div(nopat,invested),
            "NOPAT/投入资本；EBIT=SY28+SY18_1，税率=SY29/SY28",
            ["SY28","SY18_1","SY29"]+debt_codes+["ZCFZ79","ZCFZ01"],
            note="税前利润须为正；货币资金未扣除受限资金，当前为映射口径近似")
        put(out,"收现比",_div(d.get("XJ01"),rev),"XJ01/SY02",["XJ01","SY02"])
        put(out,"净现比",_div(d.get("XJ25"),net),"XJ25/(SY31优先，否则SY30)",
            ["XJ25","SY31或SY30"],note=net_note+"；净利润为负时不套用通常区间评价")
        put(out,"核心利润占比",_div(core,d.get("SY28")),"核心利润/SY28",
            ["SY02","SY07","SY16","SY17","SY17_1","SY18","SY28"],
            note="利润总额为0或核心利润组成科目缺失时不计算")
        put(out,"资产负债率",_div(d.get("ZCFZ69"),d.get("ZCFZ38")),"ZCFZ69/ZCFZ38",["ZCFZ69","ZCFZ38"])
        put(out,"有息负债率",_div(interest_debt,d.get("ZCFZ38")),
            "(ZCFZ39+ZCFZ61+ZCFZ62+ZCFZ62_12)/ZCFZ38",
            debt_codes+["ZCFZ38"],note="未纳入ZCFZ58一年内到期的非流动负债，需业务确认")
        put(out,"流动比率",_div(d.get("ZCFZ18"),d.get("ZCFZ60")),"ZCFZ18/ZCFZ60",
            ["ZCFZ18","ZCFZ60"],unit="倍")
        quick=(d["ZCFZ18"]-d["ZCFZ15"]) if d.get("ZCFZ18") is not None and d.get("ZCFZ15") is not None else None
        put(out,"速动比率",_div(quick,d.get("ZCFZ60")),"(ZCFZ18-ZCFZ15)/ZCFZ60",
            ["ZCFZ18","ZCFZ15","ZCFZ60"],unit="倍")
        interest_cover=_div((d["SY28"]+d["SY18_1"]) if d.get("SY28") is not None and d.get("SY18_1") is not None else None,d.get("SY18_1"))
        put(out,"利息保障倍数",interest_cover,"(SY28+SY18_1)/SY18_1",
            ["SY28","SY18_1"],unit="倍")
        avg_ar=((d["ANNEX09"]+d["ZCFZ06"])/2) if d.get("ANNEX09") is not None and d.get("ZCFZ06") is not None else None
        ar_turn=_div(rev,avg_ar)
        put(out,"应收账款周转天数",_div(365,ar_turn) if ar_turn else None,
            "365/(SY02/((ANNEX09+ZCFZ06)/2))",
            ["SY02","ANNEX09","ZCFZ06"],unit="天",note="按Skill公式365天制")
        avg_inv=((d["ANNEX08"]+d["ZCFZ15"])/2) if d.get("ANNEX08") is not None and d.get("ZCFZ15") is not None else None
        inv_turn=_div(cost,avg_inv)
        put(out,"存货周转天数",_div(365,inv_turn) if inv_turn else None,
            "365/(SY07/((ANNEX08+ZCFZ15)/2))",
            ["SY07","ANNEX08","ZCFZ15"],unit="天",note="按Skill公式365天制")
        put(out,"总资产周转率",_div(rev,avg_assets),"SY02/((ANNEX12+ZCFZ38)/2)",
            ["SY02","ANNEX12","ZCFZ38"],unit="次")
        # DuPont components and reconciliation are derived only when all inputs exist.
        margin=_div(net,rev); turnover=_div(rev,avg_assets); multiplier=_div(avg_assets,avg_equity)
        roe_dupont=margin*turnover*multiplier if None not in (margin,turnover,multiplier) else None
        put(out,"经典三因素杜邦ROE",roe_dupont,"净利率×总资产周转率×权益乘数",
            ["SY30或SY31","SY02","ANNEX12","ZCFZ38","ANNEX13","ZCFZ79"],
            note="权益乘数按平均总资产/平均股东权益计算，期间口径一致性需复核")
        tax_factor=_div(net,d.get("SY28"))
        interest_factor=_div(d.get("SY28"),ebit)
        operating_margin=_div(ebit,rev)
        five=tax_factor*interest_factor*operating_margin*turnover*multiplier if None not in (tax_factor,interest_factor,operating_margin,turnover,multiplier) else None
        put(out,"五因素杜邦ROE",five,"税负系数×利息负担系数×经营利润率×资产周转率×权益乘数",
            ["SY30","SY28","SY18_1","SY02","ANNEX12","ZCFZ38","ANNEX13","ZCFZ79"])
        # DSCR is deliberately labelled approximate due unmapped maintenance capex split.
        dscr_cash=(d["XJ25"]-d["XJ32"]) if d.get("XJ25") is not None and d.get("XJ32") is not None else None
        put(out,"偿债备付率（DSCR）",_div(dscr_cash,d.get("ANNEX07")),
            "(XJ25-XJ32)/ANNEX07",
            ["XJ25","XJ32","ANNEX07"],unit="倍",
            note="近似口径：XJ32是资本开支总额，未区分维持性/扩张性；ANNEX07是否等于期间应还本息额待确认")
        ebitda=(ebit+d["CF34"]+d["CF35"]+d["CF36"]) if ebit is not None and all(d.get(c) is not None for c in ["CF34","CF35","CF36"]) else None
        put(out,"有息负债/EBITDA",_div(interest_debt,ebitda),
            "有息负债/(SY28+SY18_1+CF34+CF35+CF36)",
            debt_codes+["SY28","SY18_1","CF34","CF35","CF36"],unit="倍")
        put(out,"关联资金占用率",None,"关联方其他应收款/ZCFZ79",
            ["关联方其他应收款明细","ZCFZ79"],unit="比例",
            note="Excel映射仅有ZCFZ13其他应收款总额，缺少关联方拆分，禁止用总额代替")
        results[p]=out
    return results

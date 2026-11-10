from tools.indicator_calculator import calculate_all

def sample_rows():
    vals={
      "SY02":1000,"SY07":700,"SY16":40,"SY17":50,"SY17_1":20,"SY18":10,
      "SY18_1":8,"SY28":180,"SY29":36,"SY30":144,"SY31":130,
      "ZCFZ01":100,"ZCFZ06":200,"ZCFZ13":50,"ZCFZ15":150,
      "ZCFZ18":500,"ZCFZ38":1000,"ZCFZ39":100,"ZCFZ60":300,
      "ZCFZ61":100,"ZCFZ62":20,"ZCFZ62_12":10,"ZCFZ69":600,"ZCFZ79":400,
      "XJ01":900,"XJ25":160,"XJ32":50,"CF34":20,"CF35":5,"CF36":2,
      "ANNEX07":80,"ANNEX08":140,"ANNEX09":180,"ANNEX12":900,"ANNEX13":360
    }
    return [{"report_period":"2025","field_code":k,"amount":v,"field_name":k,
             "source_system":"TEST","source_record_id":"T-"+k,"is_audited":1} for k,v in vals.items()]

def test_gross_margin_and_core_profit():
    r=calculate_all(sample_rows())["2025"]["指标"]
    assert r["毛利率"]["value"] == 0.3
    assert round(r["核心利润率"]["value"],4) == 0.18

def test_quick_ratio_and_365_day_convention():
    r=calculate_all(sample_rows())["2025"]["指标"]
    assert round(r["速动比率"]["value"],4) == 1.1667
    assert round(r["应收账款周转天数"]["value"],2) == 69.35
    assert "365/" in r["应收账款周转天数"]["formula"]

def test_related_party_ratio_is_unavailable_without_split():
    r=calculate_all(sample_rows())["2025"]["指标"]
    assert r["关联资金占用率"]["status"] == "unavailable"
    assert r["关联资金占用率"]["value"] is None

def test_dscr_is_marked_approximate():
    r=calculate_all(sample_rows())["2025"]["指标"]
    assert r["偿债备付率（DSCR）"]["status"] == "available"
    assert "近似口径" in r["偿债备付率（DSCR）"]["note"]

"""把 Mock 信贷系统接口 payload 写入 MySQL。"""
import argparse, hashlib, json
from pathlib import Path
from dotenv import load_dotenv
from db.mysql_client import connection
from mock_source import build_payload

def seed(company_id, company_name):
    # 优先读取 mock_source.py 生成的接口响应 JSON；没有文件时再本地生成。
    payload_path=Path("mock_api_payload.json")
    if payload_path.exists():
        candidate=json.loads(payload_path.read_text(encoding="utf-8"))
        if candidate.get("company",{}).get("company_id")==company_id:
            payload=candidate
        else:
            payload=build_payload(company_id,company_name)
    else:
        payload=build_payload(company_id,company_name)
    raw=json.dumps(payload,ensure_ascii=False,sort_keys=True).encode("utf-8")
    digest=hashlib.sha256(raw).hexdigest()
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO companies(company_id,company_name,industry_name)
                VALUES(%s,%s,%s) ON DUPLICATE KEY UPDATE
                company_name=VALUES(company_name), industry_name=VALUES(industry_name)""",
                (company_id,company_name,payload["company"]["industry_name"]))
            for row in payload["records"]:
                cur.execute("""INSERT INTO financial_statement_values
                    (company_id,report_period,statement_type,field_code,field_name,amount,
                     currency,source_system,source_record_id,is_audited)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE amount=VALUES(amount), field_name=VALUES(field_name),
                    source_system=VALUES(source_system), source_record_id=VALUES(source_record_id),
                    is_audited=VALUES(is_audited)""",
                    (row["company_id"],row["report_period"],row["statement_type"],row["field_code"],
                     row["field_name"],row["amount"],row["currency"],row["source_system"],
                     row["source_record_id"],row["is_audited"]))
            for year in sorted({r["report_period"] for r in payload["records"]}):
                cur.execute("""INSERT INTO data_source_events
                    (company_id,report_period,source_system,source_record_id,payload_hash)
                    VALUES(%s,%s,%s,%s,%s)""",
                    (company_id,year,payload["source_system"],f"MOCK-PAYLOAD-{company_id}-{year}",digest))
    print(f"已写入 MySQL：{company_id}，共 {len(payload['records'])} 条原始科目记录")
if __name__=="__main__":
    load_dotenv()
    ap=argparse.ArgumentParser()
    ap.add_argument("--company-id",default="DEMO001")
    ap.add_argument("--company-name",default="示例制造股份有限公司")
    a=ap.parse_args()
    seed(a.company_id,a.company_name)

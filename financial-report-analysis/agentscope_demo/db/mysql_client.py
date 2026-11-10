"""MySQL 连接与只读查询封装。所有 SQL 参数均使用占位符。"""
import os
from contextlib import contextmanager
import pymysql
from pymysql.cursors import DictCursor

def get_connection():
    return pymysql.connect(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3307")),
        user=os.getenv("MYSQL_USER", "financial_demo"),
        password=os.getenv("MYSQL_PASSWORD", "financial_demo_password"),
        database=os.getenv("MYSQL_DATABASE", "financial_demo"),
        charset=os.getenv("MYSQL_CHARSET", "utf8mb4"),
        cursorclass=DictCursor,
        autocommit=True,
        connect_timeout=5,
    )

@contextmanager
def connection():
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()

def fetch_financial_values(company_id: str, report_period: str | None = None):
    """按企业 ID 查询财务原始字段；不在 SQL 中计算财务指标。"""
    sql = """
        SELECT company_id, report_period, statement_type, field_code,
               field_name, amount, currency, source_system, source_record_id,
               is_audited
        FROM financial_statement_values
        WHERE company_id=%s
    """
    params = [company_id]
    if report_period:
        sql += " AND report_period=%s"
        params.append(str(report_period))
    sql += " ORDER BY report_period, statement_type, field_code"
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

def fetch_company(company_id: str):
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM companies WHERE company_id=%s", (company_id,))
            return cur.fetchone()

def record_analysis_run(run_id, company_id, report_period, status, started_at,
                        finished_at=None, report_path=None, audit_path=None, error_message=None):
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO analysis_runs
                (run_id, company_id, report_period, status, started_at, finished_at,
                 report_path, audit_path, error_message)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE status=VALUES(status),
                  finished_at=VALUES(finished_at), report_path=VALUES(report_path),
                  audit_path=VALUES(audit_path), error_message=VALUES(error_message)
            """, (run_id, company_id, str(report_period), status, started_at,
                  finished_at, report_path, audit_path, error_message))

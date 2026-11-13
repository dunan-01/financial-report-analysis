"""数据提供者层：统一的数据获取入口。

当前通过 mock_source 内部生成虚拟数据，不依赖外部数据库或接口。
接入真实接口时，替换对应 get_*_data 函数内部的实现逻辑即可。
"""
from mock_source import build_payload, build_related_party_payload


# ── 财报数据 ───────────────────────────────────────────────────────

def get_financial_data(company_id: str, company_name: str = "") -> dict:
    """模拟从接口获取企业财务数据。"""
    return build_payload(company_id, company_name or "示例制造股份有限公司")


def get_company_info(data: dict) -> dict:
    """从接口返回数据中提取企业信息。"""
    return data.get("company", {})


def get_financial_records(data: dict) -> list:
    """从接口返回数据中提取财务科目记录列表。"""
    return data.get("records", [])


# ── 关联交易数据 ───────────────────────────────────────────────────

def get_related_party_data(company_id: str, company_name: str = "") -> dict:
    """模拟从接口获取关联交易相关数据。

    替换为真实逻辑时，分别调用各接口：
    - 工商数据接口 → customer_info
    - 股权穿透/图谱接口 → equity_graph
    - 客户信息接口 → 基本信息
    - 交易流水接口 → transactions
    - 知识库接口 → 监管文件引用
    """
    return build_related_party_payload(company_id, company_name or "示例制造股份有限公司")


def get_related_party_company_info(data: dict) -> dict:
    return data.get("company", {})


def get_customer_info(data: dict) -> dict:
    return data.get("customer_info", {})


def get_equity_graph(data: dict) -> dict:
    return data.get("equity_graph", {})


def get_related_parties(data: dict) -> list:
    return data.get("related_parties", [])


def get_transactions(data: dict) -> list:
    return data.get("transactions", [])


def get_data_source_availability(data: dict) -> dict:
    return data.get("data_source_availability", {})


# 以下函数是各技能独立的生产接口替换边界；当前只调用 mock 构造器。
# 替换时保持 company/materials/source_system/data_source_availability 等字段契约。
from mock_source import build_skill_payload


def get_industry_data(company_id: str, company_name: str = "") -> dict:
    """行业统计/产业链/行业基准接口（当前 mock）。"""
    return build_skill_payload("industry", company_id, company_name)


def get_policy_data(company_id: str, company_name: str = "") -> dict:
    """宏观指标/监管及区域政策知识库接口（当前 mock）。"""
    return build_skill_payload("policy", company_id, company_name)


def get_risk_evidence_data(company_id: str, company_name: str = "") -> dict:
    """风险目标/财务原始记录及证据接口（当前 mock）。"""
    return build_skill_payload("risk_evidence", company_id, company_name)


def get_verification_data(company_id: str, company_name: str = "") -> dict:
    """材料/图像/流水及核验基准接口（当前 mock；图像未提供）。"""
    return build_skill_payload("verification", company_id, company_name)


def get_due_diligence_data(company_id: str, company_name: str = "") -> dict:
    """客户档案/经营/财报/走访材料聚合接口（当前 mock）。"""
    return build_skill_payload("due_diligence", company_id, company_name)


def get_equity_data(company_id: str, company_name: str = "") -> dict:
    """股权图谱/受益人/任职/关联方接口（当前 mock）。"""
    return build_skill_payload("equity", company_id, company_name)


def get_risk_extraction_data(company_id: str, company_name: str = "") -> dict:
    """风险源文档/客户档案接口（当前 mock，报告来自上游上下文）。"""
    return build_skill_payload("risk_extraction", company_id, company_name)

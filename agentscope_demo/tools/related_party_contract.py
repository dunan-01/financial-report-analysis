"""关联交易风险报告交付校验。

简单校验报告包含模板必需章节，不校验具体数值。
"""
import re
from pathlib import Path
from tools.report_presentation import presentation_errors
from tools.report_instructions import delivery_template

BASE = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = BASE.parent / "credit-related-party-detection" / "assets" / "related-party-report-template.md"


def load_template() -> str:
    return delivery_template(TEMPLATE_PATH.read_text(encoding="utf-8"))


def validate_related_party_report(report: str) -> list:
    """校验报告是否包含所有必需章节。返回错误列表。"""
    required_sections = [
        "一、报告基本信息",
        "二、关联方清单",
        "三、关联交易异常特征",
        "四、关联风险评估",
        "五、风险分级与处置建议",
        "六、一票否决触发情况",
        "七、数据缺失与降级处理",
    ]
    errors = presentation_errors(report)
    for section in required_sections:
        if section not in report:
            errors.append(f"缺少必需章节：{section}")
    forbidden = r"related_party_toolbox|company_id|source_system|字段代码|公式"
    if re.search(forbidden, report, re.I):
        errors.append("报告含技术细节，须改为客户经理业务表述。")
    return errors

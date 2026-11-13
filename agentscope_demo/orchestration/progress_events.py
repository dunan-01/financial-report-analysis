"""可选、任务隔离的过程通知；通知失败不能影响业务执行。"""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone, timedelta
import logging

_sink = ContextVar("progress_sink", default=None)
_agent = ContextVar("progress_agent", default="system")

TOOL_LABELS = {
    "query_skill_data": "查询本技能数据来源接口",
    "query_financial_data": "查询年度财务科目",
    "calculate_financial_indicators": "计算财务指标",
    "get_data_quality_summary": "检查财务数据质量",
    "get_customer_basic_info": "查询客户基本信息",
    "get_equity_penetration": "查询股权穿透图谱",
    "get_related_party_list": "查询六维关联方清单",
    "get_transaction_flows": "查询交易流水",
    "get_data_source_status": "检查数据源可用性",
}


@contextmanager
def progress_scope(*, sink=None, agent=None):
    tokens = []
    if sink is not None:
        tokens.append((_sink, _sink.set(sink)))
    if agent is not None:
        tokens.append((_agent, _agent.set(agent)))
    try:
        yield
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)


def emit(step, status, message, **details):
    sink = _sink.get()
    if sink is None:
        return
    event = dict(agent=_agent.get(), step=step, status=status, message=message,
                 time=datetime.now(timezone(timedelta(hours=8))).isoformat(), **details)
    try:
        sink(event)
    except Exception:
        logging.getLogger(__name__).warning("过程通知失败，调查继续执行。")


# 预先展示的步骤仅表示计划，不会按时间自动标记完成。
WORKFLOWS = {
    "financial": [("data", "准备财务数据与工具"), *[("tool:" + name, TOOL_LABELS[name]) for name in
        ("query_financial_data", "calculate_financial_indicators", "get_data_quality_summary")],
        ("report", "分析并生成财报报告"), ("validation", "校验及必要的报告修正")],
    "related_party": [("data", "准备关联交易数据与工具"), *[("tool:" + name, TOOL_LABELS[name]) for name in
        ("get_customer_basic_info", "get_equity_penetration", "get_related_party_list", "get_transaction_flows",
         "get_data_source_status")],
        ("report", "分析并生成关联交易报告"), ("validation", "校验及必要的报告修正")],
}


def get_workflows():
    """按当前节点定义生成网页步骤；返回副本，避免重复调用叠加依赖步骤。"""
    from orchestration.analysis_pipeline import NODES
    workflows = {key: list(steps) for key, steps in WORKFLOWS.items()}
    for node in NODES:
        if node.key not in workflows:
            workflows[node.key] = [("data", "准备技能接口"), ("tool:query_skill_data", "查询本技能数据接口"),
                                   ("report", "分析并生成报告"), ("validation", "检查交付报告")]
        plan = workflows[node.key]
        insertion = next((i for i, (key, _) in enumerate(plan) if key == "report"), len(plan))
        workflows[node.key] = plan[:insertion] + [(step["id"], step["label"]) for step in get_skill_steps(node.key)] + plan[insertion:]
        if node.upstream:
            workflows[node.key] = [("dependencies", "等待必需上游报告"), *workflows[node.key]]
    return workflows


def get_skill_steps(key):
    """从实际 Skill 执行流程中读取步骤，不从报告章节或示例推测进度。"""
    import re
    from pathlib import Path
    from orchestration.analysis_pipeline import REGISTRY
    path = Path(__file__).resolve().parents[2] / REGISTRY[key].skill / "SKILL.md"
    text = path.read_text(encoding="utf-8")
    matches = list(re.finditer(r"(?m)^###\s+步骤\s*(\d+)\s*[:：]\s*(.+)$", text))
    steps = []
    for match in matches:
        title = re.split(r"\s*\[(?:数据来源|确认机制)|[（(]数据来源", match[2])[0].strip()
        rest = text[match.end():]
        end = re.search(r"(?m)^#{1,3}\s", rest)
        steps.append({"id": "skill_step:" + match[1], "label": f"步骤{match[1]}：{title}",
                      "instructions": rest[:end.start()] if end else rest})
    if not steps:
        raise ValueError(f"技能 {key} 没有可执行的步骤定义")
    return steps


def summarize_source(payload):
    """只记录材料覆盖、来源和数量，不把原始流水/用户材料或凭证写入审计。"""
    if not isinstance(payload, dict):
        return {}
    summary = {}
    if payload.get("source_system"):
        summary["source_system"] = str(payload["source_system"])
    for key in ("records", "raw_fields", "transactions", "related_parties"):
        if isinstance(payload.get(key), list):
            summary[key + "_count"] = len(payload[key])
            sources = sorted({str(row["source_system"]) for row in payload[key] if isinstance(row, dict) and row.get("source_system")})
            if sources:
                summary["source_systems"] = sources
    if isinstance(payload.get("materials"), dict):
        summary["material_coverage"] = {key: ("未提供" if value is None or value == [] or value == {} else "已提供")
                                        for key, value in payload["materials"].items()}
    for key in ("data_source_availability", "data_sources"):
        if isinstance(payload.get(key), dict):
            summary["availability"] = {name: available for name, available in payload[key].items() if isinstance(available, bool)}
    if isinstance(payload.get("periods"), list):
        summary["report_periods"] = [str(year) for year in payload["periods"]]
    data = payload.get("data")
    if isinstance(data, dict) and ("materials" in data or "source_system" in data):
        summary.update(summarize_source(data))
    return summary


def redact_audit_text(text):
    """步骤摘要/引用展示前做基本脱敏；原始模型回复仍只放在内部诊断文件。"""
    import re
    text = re.sub(r"(?i)\bBearer\s+[^\s,;，；]+", "Bearer [已隐藏]", str(text))
    text = re.sub(r"(?i)\bsk-[A-Za-z0-9_-]{8,}", "[密钥已隐藏]", text)
    text = re.sub(r"(?i)((?:api[_-]?key|access[_-]?token|password|密钥)\s*[:=：]\s*)[^\s,;，；]+", r"\1[已隐藏]", text)
    return re.sub(r"(?<!\d)\d{11,19}(?!\d)", "[敏感号码已隐藏]", text)


# 单次调查的唯一持久化过程记录。网页 SSE 的格式从此记录转换，不另写 progress.jsonl。
class AuditTrail:
    def __init__(self, directory, args, task_id=None):
        import threading
        from pathlib import Path
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "audit.jsonl"
        self.task_id = task_id or self.directory.name
        self.context = {name: getattr(args, name, "") for name in
                        ("operator", "company_id", "company_name", "report_period", "scenario", "mode")}
        self.lock = threading.Lock()
        self.sequence = 0
        if self.path.exists():
            for event in self.read_events():
                self.sequence = max(self.sequence, event["id"])

    @staticmethod
    def to_event(record):
        return {**record, "message": record["action"], "audit": record}

    def read_events(self):
        import json
        if not self.path.exists():
            return []
        return [self.to_event(json.loads(line)) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def append(self, event):
        import json
        import re
        from pathlib import Path
        from orchestration.analysis_pipeline import REGISTRY
        node = REGISTRY.get(event.get("agent", ""))
        step = str(event.get("step", ""))
        category = ("技能业务步骤" if step.startswith("skill_step:") else "数据接口及工具调用" if step.startswith("tool:") else
                    {"dependencies": "上游材料交接", "validation": "报告交付校验", "report": "报告生成",
                     "agent": "智能体生命周期", "output": "报告归档"}.get(step, "调查任务"))
        version = "workflow"
        if node:
            text = (Path(__file__).resolve().parents[2] / node.skill / "SKILL.md").read_text(encoding="utf-8")
            match = re.search(r"(?m)^version:\s*(.+)$", text)
            version = match[1].strip() if match else "未声明"
        with self.lock:
            self.sequence += 1
            record = dict(id=self.sequence, audit_id=f"{self.task_id}:{self.sequence}", task_id=self.task_id,
                agent=event.get("agent", "system"), step=step, time=event.get("time") or datetime.now(timezone(timedelta(hours=8))).isoformat(),
                **self.context, skill=node.skill if node else "workflow", skill_version=version, category=category,
                provider=node.provider if node else None, action=event.get("message", ""), status=event.get("status", "unknown"))
            for key in ("duration", "attempt", "errors", "summary", "evidence_refs", "summary_origin", "source_summary",
                        "business_status", "tool_name", "tool_result_sha256", "input_refs", "artifact_ref", "model", "execution_mode", "upstream", "parse_method", "failure_type"):
                if key in event:
                    record[key] = event[key]
            try:
                with self.path.open("a", encoding="utf-8") as file:
                    file.write(json.dumps(record, ensure_ascii=False) + "\n")
            except OSError:
                record["persistence"] = "failed"
                logging.getLogger(__name__).warning("审计日志落盘失败，内存记录仍可导出")
            return self.to_event(record)


_run_directory = ContextVar("run_directory", default=None)


@contextmanager
def storage_scope(directory):
    token = _run_directory.set(directory)
    try:
        yield
    finally:
        _run_directory.reset(token)


def diagnostic_directory(key, fallback_root):
    from pathlib import Path
    import uuid
    root = _run_directory.get()
    path = Path(root) / "diagnostics" / key if root else Path(fallback_root) / uuid.uuid4().hex / "diagnostics" / key
    path.mkdir(parents=True, exist_ok=True)
    return path

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import run_demo
from orchestration.progress_events import AuditTrail, diagnostic_directory, emit, progress_scope, storage_scope


def args(**changes):
    return SimpleNamespace(**(dict(company_id="DEMO001", company_name="测试企业全称", report_period="2025",
        scenario="贷前尽调", operator="tester", mode="offline", user_materials={}) | changes))


def test_cli_and_web_runner_use_one_timeline_and_one_summary(monkeypatch, tmp_path):
    monkeypatch.setattr(run_demo, "RUNS", tmp_path)
    artifacts = asyncio.run(run_demo.run_analysis(args()))
    root = artifacts["summary"].parent
    assert root.parent == tmp_path
    assert artifacts["audit"] == root / "audit.jsonl"
    assert set(path.name for path in root.iterdir()) == {"reports", "summary.json", "audit.jsonl"}
    summary = json.loads((root / "summary.json").read_text())
    assert summary["run_id"] == root.name
    assert all("skill_steps" not in node and "tool_events" not in node for node in summary["agents"].values())
    trail = AuditTrail(root, args())
    events = trail.read_events()
    assert events[-1]["step"] == "job" and events[-1]["status"] == "succeeded"
    assert all(event["audit"]["task_id"] == root.name for event in events)
    raw = [json.loads(line) for line in (root / "audit.jsonl").read_text().splitlines()]
    assert all("audit" not in item and "message" not in item for item in raw)
    assert len(events) == len(raw)  # 持久化审计就是页面事件的唯一来源。


def test_invalid_period_still_gets_failure_summary_and_audit(tmp_path):
    root = tmp_path / "failed-job"
    with pytest.raises(RuntimeError, match="不存在"):
        asyncio.run(run_demo.run_analysis(args(report_period="1999"), output_dir=root))
    summary = json.loads((root / "summary.json").read_text())
    assert summary["status"] == "failed" and summary["run_id"] == root.name
    assert not (root / "audit").exists()
    assert AuditTrail(root, args()).read_events()[-1]["status"] == "failed"


def test_parallel_tasks_keep_diagnostics_under_their_own_task(tmp_path):
    async def worker(name):
        root = tmp_path / name
        with storage_scope(root):
            await asyncio.sleep(.01)
            return diagnostic_directory("financial", tmp_path / "old-unused-root")
    async def check():
        return await asyncio.gather(worker("one"), worker("two"))
    paths = asyncio.run(check())
    assert paths == [tmp_path / name / "diagnostics" / "financial" for name in ("one", "two")]
    assert not (tmp_path / "old-unused-root").exists()

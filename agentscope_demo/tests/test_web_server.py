import asyncio
import json
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from web_server import InvestigationServer, JobManager, validate_request

PARAMS = dict(company_id="DEMO001", company_name="测试制造企业", report_period="2025",
              scenario="贷前尽调", operator="TEST_OPERATOR", mode="offline")


@pytest.fixture
def service(tmp_path):
    jobs = JobManager(root=tmp_path)
    server = InvestigationServer(("127.0.0.1", 0), jobs)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", jobs
    server.shutdown()
    server.server_close()
    thread.join()
    jobs.executor.shutdown(wait=True)


def http(base, path, body=None, headers=None):
    req = Request(base + path, data=None if body is None else json.dumps(body).encode(),
                  headers=headers or ({"Content-Type": "application/json"} if body is not None else {}))
    try:
        response = urlopen(req, timeout=10)
    except HTTPError as exc:
        response = exc
    with response:
        return response.status, response.read()


def test_form_to_two_real_offline_reports(service):
    base, jobs = service
    status, page = http(base, "/")
    assert status == 200
    assert "开始调查" in page.decode()
    assert http(base, "/app.js")[0] == 200
    status, body = http(base, "/api/jobs", PARAMS)
    assert status == 202
    job_id = json.loads(body)["id"]
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        status, body = http(base, f"/api/jobs/{job_id}")
        state = json.loads(body)
        if state["status"] in {"succeeded", "failed"}:
            break
        time.sleep(.02)
    assert state["status"] == "succeeded", state
    from orchestration.analysis_pipeline import REGISTRY
    assert set(state["reports"]) == {"html", *REGISTRY}
    assert len(state["workflow"]) == 9
    assert state["summary"]["company_name"] == PARAMS["company_name"]
    assert state["event_count"] > 0
    for kind, path in state["reports"].items():
        status, report = http(base, path)
        assert status == 200 and "测试制造企业" in report.decode()
    assert b"financial" in http(base, state["reports"]["html"])[1]
    status, stream = http(base, f"/api/jobs/{job_id}/events")
    assert status == 200
    events = [json.loads(line[6:]) for line in stream.decode().splitlines() if line.startswith("data: ")]
    assert [event["id"] for event in events] == list(range(1, len(events) + 1))
    assert any(event["step"] == "output" for event in events)
    assert events[-1]["step"] == "job" and events[-1]["status"] == "succeeded"
    _, replay = http(base, f"/api/jobs/{job_id}/events", headers={"Last-Event-ID": str(events[-2]["id"])})
    assert len([line for line in replay.decode().splitlines() if line.startswith("data: ")]) == 1
    assert not (jobs.root / job_id / "progress.jsonl").exists()
    audit_file = jobs.root / job_id / "audit.jsonl"
    assert audit_file.exists()
    records = [json.loads(line) for line in audit_file.read_text().splitlines()]
    assert len(records) == len(events)
    assert records[0]["operator"] == PARAMS["operator"]
    assert records[0]["task_id"] == job_id
    assert records[-1]["status"] == "succeeded"
    summary = json.loads((jobs.root / job_id / "summary.json").read_text())
    assert summary["run_id"] == job_id
    assert summary["audit"] == "audit.jsonl"
    assert set(path.name for path in (jobs.root / job_id).iterdir()) <= {"reports", "audit.jsonl", "summary.json", "diagnostics"}
    status, exported = http(base, f"/api/jobs/{job_id}/audit")
    assert status == 200 and len(json.loads(exported)["records"]) == len(events)
    assert jobs.artifact(job_id, "audit") is None
    assert http(base, "/.env")[0] == 404
    assert http(base, "/api/jobs/../.env")[0] == 404


@pytest.mark.parametrize("changes", [
    {"company_id": "../escape"}, {"report_period": "25"}, {"mode": "shell"},
    {"operator": ""}, {"scenario": "a\nb"}, {"api_key": "secret"}, {"company_name": 23},
])
def test_invalid_form_is_rejected(changes):
    with pytest.raises(ValueError):
        validate_request(PARAMS | changes)


def test_post_rejects_external_origin_and_non_json(service):
    base, _ = service
    assert http(base, "/api/jobs", PARAMS, {"Content-Type": "application/json", "Origin": "https://external.test"})[0] == 403
    assert http(base, "/api/jobs", PARAMS, {"Content-Type": "text/plain"})[0] == 415
    assert http(base, "/api/jobs", PARAMS | {"company_id": "../bad"})[0] == 400


def test_active_job_is_not_submitted_twice_and_failure_is_safe(tmp_path):
    started, finish = threading.Event(), threading.Event()

    async def runner(args, output_dir, on_event=None, audit_trail=None):
        started.set()
        while not finish.is_set():
            await asyncio.sleep(.01)
        raise RuntimeError("secret-internal-config")

    jobs = JobManager(tmp_path, runner)
    try:
        job_id = jobs.create(validate_request(PARAMS))
        assert started.wait(2)
        with pytest.raises(RuntimeError, match="已有调查"):
            jobs.create(validate_request(PARAMS))
    finally:
        finish.set()
        jobs.executor.shutdown(wait=True)
    state = jobs.snapshot(job_id)
    assert state["status"] == "failed"
    assert "secret-internal-config" not in json.dumps(state)


def test_live_sse_disconnect_does_not_cancel_task(service):
    base, jobs = service
    entered, finish = threading.Event(), threading.Event()

    async def runner(args, output_dir, on_event=None, audit_trail=None):
        on_event(dict(agent="financial", step="data", status="running", message="获取财务数据"))
        entered.set()
        while not finish.is_set():
            await asyncio.sleep(.01)
        return {}

    jobs.runner = runner
    job_id = jobs.create(validate_request(PARAMS))
    try:
        assert entered.wait(2)
        with urlopen(base + f"/api/jobs/{job_id}/events", timeout=5) as response:
            assert response.headers["Content-Type"].startswith("text/event-stream")
            assert response.readline().startswith(b"id: 1")
            event = json.loads(response.readline().decode()[6:])
            assert event["message"] == "获取财务数据"
        assert jobs.snapshot(job_id)["status"] == "running"
    finally:
        finish.set()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and jobs.snapshot(job_id)["status"] == "running":
        time.sleep(.01)
    assert jobs.snapshot(job_id)["status"] == "succeeded"


def test_online_job_provides_planned_steps_without_marking_them_complete(service):
    base, jobs = service
    entered, finish = threading.Event(), threading.Event()

    async def runner(args, output_dir, on_event=None, audit_trail=None):
        entered.set()
        while not finish.is_set():
            await asyncio.sleep(.01)
        return {}

    jobs.runner = runner
    job_id = jobs.create(validate_request(PARAMS | {"mode": "agentscope"}))
    try:
        assert entered.wait(2)
        status, payload = http(base, f"/api/jobs/{job_id}")
        state = json.loads(payload)
        assert status == 200 and state["status"] == "running"
        assert state["event_count"] == 0
        assert len(state["workflow"]) == 9
        assert [step[0] for step in state["workflow"]["financial"] if not step[0].startswith("skill_step:")] == [
            "dependencies", "data", "tool:query_financial_data", "tool:calculate_financial_indicators",
            "tool:get_data_quality_summary", "report", "validation"]
    finally:
        finish.set()

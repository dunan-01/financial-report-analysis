"""本地调查页面：表单提交 → 后台任务 → 官方 TeamPipeline → 报告预览。

仅监听本机地址。密钥由 .env 提供，浏览器只提交业务参数；后台线程用于
防止长时间模型请求阻塞 HTTP，Agent 之间的并行仍由官方 TeamPipeline 负责。
"""
import argparse
import asyncio
import json
import hashlib
import logging
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit, parse_qs
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
from run_demo import run_analysis
from orchestration.progress_events import get_workflows, AuditTrail
from orchestration.analysis_pipeline import NODES, REGISTRY

BASE = Path(__file__).resolve().parent
STATIC = BASE / "web"
LOG = logging.getLogger(__name__)


def validate_request(data):
    """校验不可信表单值；企业 ID 还会用于文件名，禁止路径字符。"""
    if not isinstance(data, dict):
        raise ValueError("请求必须为 JSON 对象。")
    data = dict(data)
    materials = data.pop("user_materials", "")
    if not isinstance(materials, str) or len(materials) > 10000:
        raise ValueError("用户材料须为不超过10000字的文本。")
    fields = {"company_id", "company_name", "report_period", "scenario", "operator", "mode"}
    if set(data) - fields:
        raise ValueError("请求包含不支持的参数。")
    values = {}
    for name in fields:
        value = data.get(name, "")
        if not isinstance(value, str) or len(value) > 200 or any(ord(c) < 32 for c in value):
            raise ValueError("参数须为不超过 200 字的单行文本。")
        values[name] = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", values["company_id"]):
        raise ValueError("企业 ID 须为 1—64 位字母、数字、下划线或连字符。")
    if not re.fullmatch(r"[0-9]{4}", values["report_period"]):
        raise ValueError("请输入四位报告年度。")
    if values["mode"] not in {"agentscope", "offline"}:
        raise ValueError("请选择智能体调查或离线预览。")
    if not values["scenario"] or not values["operator"]:
        raise ValueError("请填写调查场景和操作人。")
    return SimpleNamespace(**values, user_materials={"text": materials} if materials.strip() else {})


class JobManager:
    def __init__(self, root=None, runner=run_analysis):
        self.root = Path(root) if root is not None else BASE / "runs"
        self.runner = runner
        self.jobs = {}
        self.lock = threading.Lock()
        self.changed = threading.Condition(self.lock)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="investigation")

    def create(self, args):
        with self.lock:
            if any(job["status"] in {"queued", "running"} for job in self.jobs.values()):
                raise RuntimeError("已有调查进行中，请等待完成后再提交。")
            job_id = uuid.uuid4().hex
            self.jobs[job_id] = {"id": job_id, "status": "queued", "mode": args.mode, "events": [], "summary": {"company_id": args.company_id, "company_name": args.company_name, "report_period": args.report_period, "scenario": args.scenario, "operator": args.operator}, "started_at": datetime.now(timezone(timedelta(hours=8))).isoformat()}
            self.jobs[job_id]["trail"] = AuditTrail(self.root / job_id, args, job_id)
            self.executor.submit(self._run, job_id, args)
            return job_id

    def record(self, job_id, event: dict[str, Any]):
        with self.changed:
            # 调查入口已写入的事件仅订阅；任务终态/测试自定义 runner 的事件在此写入同一审计。
            if "audit" not in event:
                event = self.jobs[job_id]["trail"].append(event)
            self.jobs[job_id]["events"].append(event)
            if event.get("step") == "job":
                self.jobs[job_id]["status"] = event["status"]
                self.jobs[job_id]["finished_at"] = event["time"]
            self.changed.notify_all()

    def _run(self, job_id, args):
        with self.lock:
            self.jobs[job_id]["status"] = "running"
        try:
            artifacts = asyncio.run(self.runner(args, output_dir=self.root / job_id,
                on_event=lambda event: self.record(job_id, event), audit_trail=self.jobs[job_id]["trail"]))
            with self.lock:
                self.jobs[job_id].update(artifacts=artifacts)
            self.record(job_id, dict(agent="system", step="job", status="succeeded", message="调查完成"))
        except Exception:
            # 异常堆栈仅留在本地终端，避免把客户端配置等内部内容传回页面。
            LOG.exception("调查失败，任务 %s", job_id)
            with self.lock:
                self.jobs[job_id].update(
                    error="调查未完成。请检查本地服务终端及任务审计记录；核对模型配置、网络和报告期。",
                )

            self.record(job_id, dict(agent="system", step="job", status="failed", message="调查失败，请查看步骤日志"))

    def snapshot(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                return None
            result = {key: value for key, value in job.items() if key not in {"artifacts", "events", "trail"}}
            result["workflow"] = get_workflows()
            result["nodes"] = [{"key": node.key, "label": node.label, "upstream": list(node.upstream)} for node in NODES]
            result["audit_url"] = f"/api/jobs/{job_id}/audit"
            result["event_count"] = len(job["events"])
            if job["status"] == "succeeded":
                result["reports"] = {
                    kind: f"/api/jobs/{job_id}/reports/{kind}"
                    for kind in ("html", *REGISTRY) if kind in job["artifacts"]
                }
            return result

    def artifact(self, job_id, kind):
        with self.lock:
            job = self.jobs.get(job_id)
            if not job or job["status"] != "succeeded" or kind not in {"html", *REGISTRY}:
                return None
            return Path(job["artifacts"][kind])


class InvestigationServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, jobs):
        self.jobs = jobs
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    @property
    def app(self) -> InvestigationServer:
        assert isinstance(self.server, InvestigationServer)
        return self.server

    def _local_request(self):
        expected = {f"127.0.0.1:{self.app.server_port}", f"localhost:{self.app.server_port}"}
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        if host not in expected or (origin and origin != f"http://{host}"):
            self._json(403, {"error": "仅允许本机页面请求。"})
            return False
        return True

    def _send(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status, data):
        self._send(status, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def do_GET(self):
        if not self._local_request():
            return
        path = urlsplit(self.path).path
        assets = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"), "/app.css": ("app.css", "text/css")}
        if path in assets:
            name, mime = assets[path]
            self._send(200, (STATIC / name).read_bytes(), mime + "; charset=utf-8")
            return
        audit_match = re.fullmatch(r"/api/jobs/([0-9a-f]{32})/audit", path)
        if audit_match:
            job_id = audit_match.group(1)
            with self.app.jobs.lock:
                job = self.app.jobs.jobs.get(job_id)
                records = [dict(event["audit"]) for event in job["events"]] if job else None
            if records is None:
                self._json(404, {"error": "任务不存在"})
            else:
                self._json(200, {"task_id": job_id, "records": records})
            return
        stream = re.fullmatch(r"/api/jobs/([0-9a-f]{32})/events", path)
        if stream:
            self._stream(stream.group(1))
            return
        match = re.fullmatch(r"/api/jobs/([0-9a-f]{32})(?:/reports/(html|[a-z_]+))?", path)
        if match:
            job_id, kind = match.groups()
            if kind:
                artifact = self.app.jobs.artifact(job_id, kind)
                if artifact and artifact.is_file():
                    mime = "text/html" if kind == "html" else "text/plain"
                    self._send(200, artifact.read_bytes(), mime + "; charset=utf-8")
                    return
            else:
                job = self.app.jobs.snapshot(job_id)
                if job:
                    self._json(200, job)
                    return
        self._json(404, {"error": "任务或文件不存在，服务重启后请重新发起调查。"})

    def _stream(self, job_id):
        if self.app.jobs.snapshot(job_id) is None:
            self._json(404, {"error": "任务不存在"})
            return
        try:
            after = int(self.headers.get("Last-Event-ID") or parse_qs(urlsplit(self.path).query).get("after", ["0"])[0])
            if after < 0:
                raise ValueError()
        except ValueError:
            self._json(400, {"error": "事件序号无效"})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            while True:
                with self.app.jobs.changed:
                    job = self.app.jobs.jobs[job_id]
                    pending = list(job["events"][after:])
                    terminal = job["status"] in {"succeeded", "failed"}
                    if not pending and not terminal:
                        self.app.jobs.changed.wait(timeout=5)
                for event in pending:
                    payload = json.dumps(event, ensure_ascii=False)
                    self.wfile.write(f"id: {event['id']}\ndata: {payload}\n\n".encode())
                    after = event["id"]
                self.wfile.write(b": heartbeat\n\n")
                self.wfile.flush()
                if terminal:
                    break
        except (BrokenPipeError, ConnectionResetError):
            pass  # 浏览器离线只关闭订阅，不取消后台任务。

    def do_POST(self):
        if not self._local_request():
            return
        if self.path != "/api/jobs":
            self._json(404, {"error": "接口不存在。"})
            return
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            self._json(415, {"error": "请使用 JSON 提交调查参数。"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 16384:
                raise ValueError("请求内容为空或过大。")
            args = validate_request(json.loads(self.rfile.read(length)))
        except (ValueError, UnicodeError):
            self._json(400, {"error": "参数无效，请检查企业 ID、四位报告年度、调查场景和操作人。"})
            return
        try:
            job_id = self.app.jobs.create(args)
        except RuntimeError as exc:
            self._json(409, {"error": str(exc)})
            return
        self._json(202, {"id": job_id, "status": "queued"})


def main():
    parser = argparse.ArgumentParser(description="启动本地财报及关联交易调查页面")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    load_dotenv(BASE / ".env")
    logging.basicConfig(level=logging.INFO)
    jobs = JobManager()
    server = InvestigationServer(("127.0.0.1", args.port), jobs)
    print(f"调查页面：http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        jobs.executor.shutdown(wait=True)


if __name__ == "__main__":
    main()

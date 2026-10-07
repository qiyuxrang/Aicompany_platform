"""Authenticated deterministic loopback model responder; never forwards requests."""
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time

class Gateway:
    def __init__(self, token):
        self.token = token
        self.lock = threading.Lock()
        self.active = threading.Condition()
        self.active_handlers = 0
        self.calls = Counter()
        self.errors = []
        self.rejected = 0
        self.disconnected = 0
        self.hold_model = None
        self.entered = threading.Event()
        self.release = threading.Event()
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                if self.path == "/__fault__/health" and self.headers.get("Authorization") == "Bearer " + owner.token:
                    return self.reply(200, {"synthetic": True, "status": "ready"})
                owner.rejected += 1
                self.reply(403, {"code": "unauthorized"})
            def do_POST(self):
                with owner.active:
                    owner.active_handlers += 1
                try:
                    self.handle_model()
                finally:
                    with owner.active:
                        owner.active_handlers -= 1
                        owner.active.notify_all()
            def handle_model(self):
                self.connection.settimeout(10)
                if self.path != "/v1/generate" or self.headers.get("Authorization") != "Bearer " + owner.token:
                    owner.rejected += 1
                    return self.reply(403, {"code": "unauthorized"})
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= 1048576:
                        raise ValueError("bounded_request_required")
                    request = json.loads(self.rfile.read(size))
                    model = request["model"]["model_name"]
                    with owner.lock:
                        owner.calls[model] += 1
                        hold = owner.hold_model == model
                        if hold:
                            owner.hold_model = None
                            owner.entered.set()
                    if hold and not owner.release.wait(30):
                        raise TimeoutError("fault_coordinator_did_not_release")
                    if model == "hr_resume_parse":
                        value = {"skills": {"value": ["SQL"], "status": "extracted", "source_ref": {"quote": "技能：SQL"}}}
                    elif model == "hr_match_summary":
                        value = {"requirements": [{"requirement_id": "skill_requirements#0", "verdict": "MATCH", "evidence": [{"quote": "技能：SQL"}]}]}
                    elif model == "product_blueprint":
                        payload = json.loads(request["messages"][-1]["content"])
                        value = {"purpose": "隔离故障蓝图", "audience": "合成用户", "chapters": [{"id": "chapter-1", "title": "合成章节", "scope": "核对合成清单", "source_ids": payload["allowed_source_ids"]}], "conditions": [{"text": "不新增清单外设备", "type": "program"}], "missing": [], "conflicts": [], "template_version": "frozen-original-v1"}
                    else:
                        raise ValueError("route_not_allowed")
                    self.reply(200, {"content": json.dumps(value, ensure_ascii=False), "duration_ms": 1, "prompt_tokens": None, "completion_tokens": None})
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    owner.disconnected += 1
                except Exception as error:
                    owner.errors.append(type(error).__name__)
                    self.reply(502, {"code": "invalid_response"})
            def reply(self, status, value):
                body = json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
    def arm(self, model):
        if model not in ("hr_resume_parse", "product_blueprint"):
            raise ValueError("fault_route_not_allowed")
        self.entered.clear()
        self.release.clear()
        self.hold_model = model
    def __enter__(self):
        self.thread.start()
        self.url = "http://127.0.0.1:" + str(self.server.server_address[1])
        return self
    def __exit__(self, *args):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        deadline = time.monotonic() + 15
        with self.active:
            while self.active_handlers:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("owned_gateway_handlers_not_drained")
                self.active.wait(remaining)
        if self.thread.is_alive():
            raise RuntimeError("owned_gateway_shutdown_failed")

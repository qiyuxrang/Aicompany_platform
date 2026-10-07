"""Deterministic loopback gateway fixture, never forwards to any provider."""
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time


class MockGateway:
    def __init__(self, token):
        self.token = token
        self.lock = threading.Lock()
        self.calls = Counter()
        self.active = 0
        self.max_active = 0
        self.first_hr_timeout = True
        self.hr_pair = threading.Event()
        self.hr_parse_active = 0
        self.max_hr_parse_active = 0
        self.hold_product = False
        self.product_entered = threading.Event()
        self.product_release = threading.Event()
        self.rejected = 0
        self.errors = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                started = time.monotonic()
                if self.path != "/v1/generate" or self.headers.get("Authorization") != "Bearer " + outer.token:
                    outer.rejected += 1
                    return self.reply(403, {"code": "unauthorized"})
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 1048576:
                        raise ValueError()
                    request = json.loads(self.rfile.read(length))
                    model = request["model"]["model_name"]
                    with outer.lock:
                        outer.calls[model] += 1
                        outer.active += 1
                        outer.max_active = max(outer.max_active, outer.active)
                        timeout = model == "hr_resume_parse" and outer.first_hr_timeout
                        if timeout:
                            outer.first_hr_timeout = False
                    try:
                        if timeout:
                            return self.reply(504, {"code": "timeout"})
                        if model == "hr_resume_parse":
                            # Real worker calls overlap on this network barrier; no fake worker sleep/task.
                            with outer.lock:
                                outer.hr_parse_active += 1
                                outer.max_hr_parse_active = max(outer.max_hr_parse_active, outer.hr_parse_active)
                                if outer.hr_parse_active >= 2:
                                    outer.hr_pair.set()
                            outer.hr_pair.wait(timeout=2)
                            with outer.lock:
                                outer.hr_parse_active -= 1
                            content = {"skills": {"value": ["SQL"], "status": "extracted",
                                                  "source_ref": {"quote": "技能：SQL"}}}
                        elif model == "hr_match_summary":
                            content = {"requirements": [{"requirement_id": "skill_requirements#0", "verdict": "MATCH",
                                                        "evidence": [{"quote": "技能：SQL"}]}]}
                        elif model == "product_blueprint":
                            if outer.hold_product:
                                outer.product_entered.set()
                                if not outer.product_release.wait(timeout=20):
                                    raise TimeoutError()
                            payload = json.loads(request["messages"][-1]["content"])
                            content = {"purpose": "隔离合成蓝图预览", "audience": "合成用户", "chapters": [
                                {"id": "chapter-1", "title": "合成章节", "scope": "仅核对输入清单",
                                 "source_ids": payload["allowed_source_ids"]}],
                                "conditions": [{"text": "不新增清单外设备", "type": "program"}],
                                "missing": [], "conflicts": [], "template_version": "frozen-original-v1"}
                        else:
                            raise ValueError("unsupported synthetic route")
                        return self.reply(200, {"content": json.dumps(content, ensure_ascii=False),
                            "duration_ms": int((time.monotonic() - started) * 1000),
                            "prompt_tokens": None, "completion_tokens": None})
                    finally:
                        with outer.lock:
                            outer.active -= 1
                except Exception as error:
                    outer.errors.append(type(error).__name__)
                    self.reply(502, {"code": "invalid_response"})

            def reply(self, status, value):
                body = json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        return self

    def __exit__(self, *args):
        self.product_release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise RuntimeError("owned_gateway_shutdown_failed")

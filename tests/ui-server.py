"""Test-only loopback harness: real MapDesk API/store, no real profile or token."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import mapdesk_runtime as runtime
from mapdesk_core import WorkspaceStore
spec = importlib.util.spec_from_file_location("mapdesk_ui_test_api", ROOT / "dashboard/plugin_api.py")
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)
bundle = Path(sys.argv[1]).read_bytes()
with tempfile.TemporaryDirectory(prefix="mapdesk-ui-", dir=os.environ["TMPDIR"]) as directory:
    runtime._store = lambda: WorkspaceStore(Path(directory))
    api._RUNTIME = runtime
    api.public_token = lambda: ""
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client:
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self, code, body, content_type):
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/ui.js":
                    return self.respond(200, bundle, "application/javascript")
                if self.path.startswith("/api/"):
                    result = client.get(self.path[4:])
                    return self.respond(result.status_code, result.content, "application/json")
                html = b'<!doctype html><html><head><style>:root{--ui-text-primary:#e5edf5;--ui-text-tertiary:#9aabc0;--ui-stroke-secondary:#344557;--ui-accent:#68c8bd}body{margin:0;background:#111923;color:var(--ui-text-primary);font:13px system-ui}#root{height:100vh}button,input,select,textarea{font:inherit}button{cursor:pointer}button:disabled{cursor:default;opacity:.5}select{background:#172432!important}button{background:#203044;color:inherit;border:1px solid #42546a;border-radius:5px;padding:6px 9px}a{color:#84c9ff}*{box-sizing:border-box}</style></head><body><div id="root"></div><script src="/ui.js"></script></body></html>'
                self.respond(200, html, "text/html")

            def do_POST(self):
                if self.path == "/__stop__":
                    self.respond(200, b"{}", "application/json")
                    threading.Thread(target=self.server.shutdown).start()
                    return
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                result = client.post(self.path[4:], content=body, headers={"Content-Type": "application/json"})
                self.respond(result.status_code, result.content, "application/json")

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        print(json.dumps({"port": server.server_address[1]}), flush=True)
        try:
            server.serve_forever()
        finally:
            server.server_close()

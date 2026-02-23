#!/usr/bin/env python3
"""
Agent-trace file viewer backend — stdlib HTTP server.

Serves:
  - /api/project   — project root, storage, has_agent_trace
  - /api/health    — health check
  - /api/tree      — list dirs/files (?path=...)
  - /api/file      — file content (?path=...)
  - /api/git-blame — git blame segments (?path=...)
  - /api/agent-trace-blame — agent-trace blame (?path=...)
  - /              — static frontend (index.html + assets)

Bind: 127.0.0.1:8765. Project root passed as first CLI arg (default: cwd).
Reuses agent-trace CLI lib when ~/.agent-trace/lib is present (for agent-trace blame).
"""
from __future__ import annotations

import json
import os
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

# Project root (set from argv)
PROJECT_ROOT = os.getcwd()
STATIC_DIR: str | None = None  # Set to frontend dist path when serving static

# Add ~/.agent-trace/lib to sys.path so we can import agent_trace.blame + config when present
_AGENT_TRACE_LIB = os.path.expanduser("~/.agent-trace/lib")
if os.path.isdir(_AGENT_TRACE_LIB) and _AGENT_TRACE_LIB not in sys.path:
    sys.path.insert(0, _AGENT_TRACE_LIB)


def resolve_path(project_root: str, rel_path: str) -> str | None:
    """Resolve rel_path under project_root; return None if outside root (path traversal)."""
    root = os.path.abspath(project_root)
    full = os.path.normpath(os.path.join(root, rel_path.lstrip("/")))
    if not full.startswith(root):
        return None
    return full


class ViewerHandler(BaseHTTPRequestHandler):
    def _send_json(self, data: dict, status: int = 200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text: str, content_type: str, status: int = 200):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, message: str, status: int = 400):
        self._send_json({"error": message}, status=status)

    def _api_project(self):
        from .routes.project import get_project_info
        info = get_project_info(PROJECT_ROOT)
        self._send_json(info)

    def _api_health(self):
        self._send_json({"status": "ok"})

    def _api_tree(self):
        from .routes.tree import get_tree
        query = parse_qs(urlparse(self.path).query)
        path = (query.get("path") or [""])[0]
        entries = get_tree(PROJECT_ROOT, path)
        self._send_json({"path": path, "entries": entries})

    def _api_file(self):
        from .routes.file_route import safe_read_file
        query = parse_qs(urlparse(self.path).query)
        path = (query.get("path") or [""])[0]
        if not path:
            self._send_error_json("path required", status=400)
            return
        content, content_type = safe_read_file(PROJECT_ROOT, path)
        if content is None:
            self._send_error_json("file not found or binary", status=404)
            return
        self._send_text(content, content_type or "text/plain; charset=utf-8")

    def _api_git_blame(self):
        from .routes.git_blame import get_git_blame
        query = parse_qs(urlparse(self.path).query)
        path = (query.get("path") or [""])[0]
        if not path:
            self._send_error_json("path required", status=400)
            return
        segments = get_git_blame(PROJECT_ROOT, path)
        if segments is None:
            self._send_error_json("file not found or not a git repo", status=404)
            return
        self._send_json({"path": path, "segments": segments})

    def _api_agent_trace_blame(self):
        from .routes.agent_trace_blame import get_agent_trace_blame
        query = parse_qs(urlparse(self.path).query)
        path = (query.get("path") or [""])[0]
        if not path:
            self._send_error_json("path required", status=400)
            return
        data, err, status = get_agent_trace_blame(PROJECT_ROOT, path)
        if data is not None:
            self._send_json(data)
            return
        self._send_error_json(err or "blame failed", status=status)

    def _api_conversation(self):
        from .routes.conversation import get_conversation_content
        query = parse_qs(urlparse(self.path).query)
        # In local mode, path= is accepted as well as url= (file:// or bare path)
        url = (query.get("url") or query.get("path") or [""])[0]
        if not url:
            self._send_error_json("url or path required", status=400)
            return
        result, err, status = get_conversation_content(PROJECT_ROOT, url)
        if result is not None:
            self._send_json(result)
            return
        self._send_error_json(err or "not found", status=status)

    def _serve_fallback_html(self):
        """Minimal HTML that uses the API when frontend is not built."""
        html = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>Agent-Trace Viewer</title></head><body>
<h1>Agent-Trace Viewer</h1>
<p>Project: <span id="root">...</span></p>
<p>Storage: <span id="storage">...</span> | Agent-trace: <span id="has">...</span></p>
<h2>Files</h2><ul id="tree"></ul>
<h2>File content</h2><pre id="content">Select a file.</pre>
<script>
var API = '';
fetch(API + '/api/project').then(r=>r.json()).then(function(p){ document.getElementById('root').textContent = p.root; document.getElementById('storage').textContent = p.storage; document.getElementById('has').textContent = p.has_agent_trace; });
fetch(API + '/api/tree?path=').then(r=>r.json()).then(function(d){ var ul = document.getElementById('tree'); (d.entries||[]).forEach(function(e){ var li = document.createElement('li'); var a = document.createElement('a'); a.href='#'+e.path; a.textContent = (e.type==='dir'?'[dir] ':'')+e.name; a.onclick = function(){ fetch(API+'/api/file?path='+encodeURIComponent(e.path)).then(r=>r.text()).then(function(t){ document.getElementById('content').textContent = t; }); return false; }; li.appendChild(a); ul.appendChild(li); }); });
</script>
</body></html>"""
        self._send_text(html, "text/html; charset=utf-8")

    def _serve_static(self, path: str):
        """Serve static file from STATIC_DIR or return fallback HTML."""
        if not STATIC_DIR or not os.path.isdir(STATIC_DIR):
            self._serve_fallback_html()
            return
        path = path.lstrip("/")
        if not path:
            path = "index.html"
        full = os.path.normpath(os.path.join(STATIC_DIR, path))
        if not full.startswith(os.path.abspath(STATIC_DIR)):
            self.send_response(404)
            self.end_headers()
            return
        if os.path.isdir(full):
            full = os.path.join(full, "index.html")
        if not os.path.isfile(full):
            # SPA fallback: serve index.html for any non-file route
            index = os.path.join(STATIC_DIR, "index.html")
            if os.path.isfile(index):
                with open(index) as f:
                    self._send_text(f.read(), "text/html; charset=utf-8")
                return
            self.send_response(404)
            self.end_headers()
            return
        try:
            with open(full, "rb") as f:
                data = f.read()
        except OSError:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        if full.endswith(".html"):
            self.send_header("Content-Type", "text/html; charset=utf-8")
        elif full.endswith(".js"):
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
        elif full.endswith(".css"):
            self.send_header("Content-Type", "text/css; charset=utf-8")
        else:
            self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        if path == "/api/project":
            self._api_project()
        elif path == "/api/health":
            self._api_health()
        elif path == "/api/tree":
            self._api_tree()
        elif path == "/api/file":
            self._api_file()
        elif path == "/api/git-blame":
            self._api_git_blame()
        elif path == "/api/agent-trace-blame":
            self._api_agent_trace_blame()
        elif path == "/api/conversation":
            self._api_conversation()
        elif path.startswith("/api/"):
            self._send_error_json("not found", status=404)
        else:
            self._serve_static(parsed.path or "/")

    def log_message(self, format, *args):
        # Quiet logs unless needed
        pass


def main():
    global PROJECT_ROOT, STATIC_DIR
    project_arg = (sys.argv[1:] or [None])[0]
    if project_arg and os.path.isdir(project_arg):
        PROJECT_ROOT = os.path.abspath(project_arg)
    # Static dir: backend/../frontend/dist or backend/../dist (viewer root = parent of backend)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    viewer_root = os.path.dirname(script_dir)
    for candidate in [
        os.path.join(viewer_root, "frontend", "dist"),
        os.path.join(viewer_root, "dist"),
    ]:
        if os.path.isdir(candidate):
            STATIC_DIR = os.path.abspath(candidate)
            break
    port = 8765
    server = HTTPServer(("127.0.0.1", port), ViewerHandler)
    print(f"Viewer: http://127.0.0.1:{port} (project: {PROJECT_ROOT})")
    server.serve_forever()


if __name__ == "__main__":
    main()

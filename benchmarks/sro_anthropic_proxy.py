#!/usr/bin/env python3
"""Local Anthropic-compatible proxy for Claude Code benchmarks.

Claude Code validates the selected model with ``GET /v1/models/{id}``, which
Paratera's gateway rejects with 401.  This proxy answers the model-listing
endpoints locally and forwards ``/v1/messages`` to the upstream provider so
Claude Code can run against Paratera models (DeepSeek-V4-Flash etc.).

Usage:
  UPSTREAM_BASE=https://llmapi.paratera.com/v1 UPSTREAM_KEY=$DEEPSEEK_API_KEY \
    python3 local_agent_comp/sro_anthropic_proxy.py --port 18766
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


UPSTREAM_BASE = os.environ.get("UPSTREAM_BASE", "https://llmapi.paratera.com/v1")
UPSTREAM_KEY = os.environ.get("UPSTREAM_KEY", os.environ.get("DEEPSEEK_API_KEY", ""))
ALLOWED_MODELS = {
    "DeepSeek-V4-Flash",
    "DeepSeek-V4-Pro",
    "GLM-5.2",
    "Qwen3.6-Plus",
    "Kimi-K2.5",
}


def _upstream(path: str, *, method: str = "GET", body: bytes | None = None) -> tuple[int, bytes, str]:
    url = UPSTREAM_BASE.rstrip("/") + path
    headers = {
        "Authorization": f"Bearer {UPSTREAM_KEY}",
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, resp.read(), resp.headers.get("content-type", "application/json")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), "application/json"


def _strip_v1(path: str) -> str:
    """Strip the /v1 prefix; UPSTREAM_BASE already includes it."""
    if path.startswith("/v1"):
        return path[3:] or "/"
    return path


class ProxyHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        if os.environ.get("SRO_PROXY_LOG") == "1":
            super().log_message(fmt, *args)

    def _send(self, status: int, payload: bytes, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path.rstrip("/") == "/v1/models":
            data = {"data": [{"id": model, "object": "model", "owned_by": "proxy"} for model in sorted(ALLOWED_MODELS)]}
            self._send(200, json.dumps(data).encode())
            return
        prefix = "/v1/models/"
        if path.startswith(prefix):
            model = path[len(prefix) :]
            data = {"id": model, "object": "model", "owned_by": "proxy"}
            self._send(200, json.dumps(data).encode())
            return
        status, body, content_type = _upstream(_strip_v1(path))
        self._send(status, body, content_type)

    def do_HEAD(self) -> None:
        # Claude Code probes the provider with HEAD /v1/api/hello; answer
        # locally so the endpoint is recognized as Anthropic-compatible.
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        path = self.path.split("?", 1)[0]
        if path.rstrip("/") == "/v1/messages":
            status, payload, content_type = _upstream(
                "/messages", method="POST", body=body
            )
            self._send(status, payload, content_type)
            return
        status, payload, content_type = _upstream(_strip_v1(path), method="POST", body=body)
        self._send(status, payload, content_type)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18766)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    if not UPSTREAM_KEY:
        raise SystemExit("missing UPSTREAM_KEY or DEEPSEEK_API_KEY")
    server = ThreadingHTTPServer((args.host, args.port), ProxyHandler)
    print(f"[sro-proxy] listening on http://{args.host}:{args.port} -> {UPSTREAM_BASE}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

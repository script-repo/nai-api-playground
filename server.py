#!/usr/bin/env python3
"""Local API playground for Nutanix Enterprise AI 2.8.

Serves the static UI and proxies calls to a NAI cluster so the browser
is not blocked by CORS. Binds to loopback only.
"""

from __future__ import annotations

import argparse
import base64
import json
import shutil
import ssl
import subprocess
import tempfile
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
MAX_RESPONSE = 8 * 1024 * 1024
MAX_REQUEST = 25 * 1024 * 1024

HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "content-length",
}


def origin_of(base_url: str) -> str:
    parsed = urlparse(base_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Base URL must be an http or https origin, such as https://nai.lab.local")
    return f"{parsed.scheme}://{parsed.netloc}"


def target_url(base_url: str, path: str, query: dict | None) -> str:
    path = (path or "").strip()
    if path.startswith(("http://", "https://")):
        parsed = urlparse(path)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Full URL targets must be http or https")
        url = path
    else:
        if not path.startswith("/"):
            path = "/" + path
        url = origin_of(base_url) + path
    if query:
        pairs = []
        for key, value in query.items():
            if value is None or value == "":
                continue
            if isinstance(value, list):
                for item in value:
                    pairs.append((key, item))
            else:
                pairs.append((key, value))
        if pairs:
            joiner = "&" if "?" in url else "?"
            url = url + joiner + urlencode(pairs)
    return url


def forward_via_curl(url: str, method: str, headers: dict, data: bytes | None, timeout: float, insecure: bool) -> tuple[int, dict, bytes]:
    """Retry through curl when Python's OpenSSL handshake is rejected.

    Anthropic's edge rejects this interpreter's TLS fingerprint. curl on
    Windows uses the system TLS stack and completes the handshake.
    """
    curl = shutil.which("curl") or shutil.which("curl.exe")
    if not curl:
        raise URLError("Python TLS handshake failed and curl is not installed")
    with tempfile.TemporaryDirectory() as directory:
        header_path = str(Path(directory) / "headers")
        body_path = str(Path(directory) / "body")
        command = [
            curl,
            "-sS",
            "--ssl-no-revoke",
            "-X",
            method,
            "--max-time",
            str(max(1, int(timeout))),
            "-D",
            header_path,
            "-o",
            body_path,
            "-w",
            "%{http_code}",
        ]
        if insecure:
            command.append("-k")
        for key, value in headers.items():
            command.extend(["-H", f"{key}: {value}"])
        if data is not None:
            data_path = str(Path(directory) / "request-body")
            Path(data_path).write_bytes(data)
            command.extend(["--data-binary", f"@{data_path}"])
        command.append(url)
        completed = subprocess.run(command, capture_output=True)
        status_text = completed.stdout.decode("ascii", "replace").strip()
        if completed.returncode != 0 or not status_text.isdigit() or status_text == "000":
            detail = completed.stderr.decode("utf-8", "replace").strip() or "curl failed"
            raise URLError(detail)
        header_text = Path(header_path).read_text(encoding="utf-8", errors="replace") if Path(header_path).exists() else ""
        blocks = [block for block in header_text.split("\r\n\r\n") if block.strip()]
        parsed_headers: dict[str, str] = {}
        if blocks:
            for line in blocks[-1].splitlines()[1:]:
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                parsed_headers[key.strip()] = value.strip()
        raw = Path(body_path).read_bytes() if Path(body_path).exists() else b""
        return int(status_text), parsed_headers, raw


def encode_multipart(fields: dict, file_info: dict | None) -> tuple[bytes, str]:
    boundary = "----nai" + uuid.uuid4().hex
    chunks: list[bytes] = []
    for name, value in (fields or {}).items():
        if value is None or value == "":
            continue
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        chunks.append(str(value).encode())
        chunks.append(b"\r\n")
    if file_info and file_info.get("base64"):
        raw = base64.b64decode(file_info["base64"])
        filename = file_info.get("name") or "upload.bin"
        content_type = file_info.get("type") or "application/octet-stream"
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(
            (
                f'Content-Disposition: form-data; name="{file_info.get("field") or "file"}"; '
                f'filename="{filename}"\r\n'
            ).encode()
        )
        chunks.append(f"Content-Type: {content_type}\r\n\r\n".encode())
        chunks.append(raw)
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


class Handler(BaseHTTPRequestHandler):
    server_version = "nai-playground/2.8"

    def log_message(self, fmt: str, *args) -> None:
        message = fmt % args
        if "Authorization" in message or "Bearer" in message or "Basic" in message:
            return
        print(f"[{self.log_date_time_string()}] {message}")

    def _send(self, status: int, payload: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, status: int, data: dict) -> None:
        self._send(status, json.dumps(data).encode(), "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/health":
            self._json(200, {"ok": True})
            return
        if path == "/":
            path = "/index.html"
        file_path = (STATIC / path.lstrip("/")).resolve()
        if not str(file_path).startswith(str(STATIC.resolve())) or not file_path.is_file():
            self._json(404, {"error": "Not found"})
            return
        content_type = "application/octet-stream"
        if file_path.suffix == ".html":
            content_type = "text/html; charset=utf-8"
        elif file_path.suffix == ".json":
            content_type = "application/json; charset=utf-8"
        elif file_path.suffix == ".css":
            content_type = "text/css; charset=utf-8"
        elif file_path.suffix == ".js":
            content_type = "text/javascript; charset=utf-8"
        self._send(200, file_path.read_bytes(), content_type)

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/proxy":
            self._json(404, {"error": "Not found"})
            return
        length = int(self.headers.get("Content-Length") or "0")
        if length > MAX_REQUEST:
            self._json(413, {"error": "Request is too large"})
            return
        try:
            incoming = json.loads(self.rfile.read(length).decode() or "{}")
        except json.JSONDecodeError:
            self._json(400, {"error": "Proxy body must be JSON"})
            return
        try:
            result = forward(incoming)
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
            return
        except URLError as exc:
            self._json(502, {"error": f"Could not reach the cluster: {exc.reason}"})
            return
        except TimeoutError:
            self._json(504, {"error": "The cluster did not respond before the timeout"})
            return
        except OSError as exc:
            self._json(502, {"error": f"Could not reach the cluster: {exc}"})
            return
        self._json(200, result)


def forward(incoming: dict) -> dict:
    import time

    method = (incoming.get("method") or "GET").upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}:
        raise ValueError(f"Unsupported method {method}")
    url = target_url(incoming.get("baseUrl") or "", incoming.get("path") or "/", incoming.get("query") or {})
    headers = {}
    for key, value in (incoming.get("headers") or {}).items():
        if not key or value is None or value == "":
            continue
        headers[str(key)] = str(value)

    data = None
    if method not in {"GET", "HEAD"} and incoming.get("bodyKind") == "multipart":
        data, content_type = encode_multipart(incoming.get("fields") or {}, incoming.get("file"))
        headers["Content-Type"] = content_type
    elif method not in {"GET", "HEAD"}:
        body = incoming.get("body")
        if body not in (None, ""):
            data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
            headers.setdefault("Content-Type", "application/json")

    request = Request(url, data=data, headers=headers, method=method)
    context = None
    if urlparse(url).scheme == "https" and incoming.get("insecure"):
        context = ssl._create_unverified_context()
    timeout = float(incoming.get("timeout") or 120)
    started = time.perf_counter()
    status = 0
    resp_headers = {}
    raw = b""
    open_kwargs = {"timeout": timeout}
    if context is not None:
        open_kwargs["context"] = context
    try:
        with urlopen(request, **open_kwargs) as response:
            status = response.status
            resp_headers = dict(response.headers.items())
            raw = response.read(MAX_RESPONSE + 1)
    except HTTPError as exc:
        status = exc.code
        resp_headers = dict(exc.headers.items()) if exc.headers else {}
        raw = exc.read(MAX_RESPONSE + 1)
    except URLError as exc:
        reason = str(exc.reason).lower()
        if "handshake" not in reason and "ssl" not in reason:
            raise
        status, resp_headers, raw = forward_via_curl(
            url,
            method,
            headers,
            data,
            timeout,
            bool(incoming.get("insecure")),
        )
    elapsed = int((time.perf_counter() - started) * 1000)
    truncated = len(raw) > MAX_RESPONSE
    raw = raw[:MAX_RESPONSE]
    try:
        text = raw.decode("utf-8")
        binary = False
    except UnicodeDecodeError:
        text = base64.b64encode(raw).decode("ascii")
        binary = True
    safe_headers = {
        key: value
        for key, value in resp_headers.items()
        if key.lower() not in HOP_BY_HOP
    }
    return {
        "url": url,
        "status": status,
        "elapsedMs": elapsed,
        "headers": safe_headers,
        "body": text,
        "binary": binary,
        "truncated": truncated,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="NAI 2.8 API playground")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--open", action="store_true", help="Open the playground in a browser")
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("Refusing to bind outside loopback. This proxy holds cluster credentials.")
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}"
    print(f"NAI 2.8 playground at {url}", flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped")


if __name__ == "__main__":
    main()

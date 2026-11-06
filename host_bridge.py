#!/usr/bin/env python3
"""Host-side bridge for the Kali VM agent.

The Windows host only listens on 127.0.0.1:8899, so Windows Defender Firewall
never sees an inbound connection (VMnet8 is fully blocked from the VM). The
reachability comes from run_bridge.ps1, which keeps an SSH reverse tunnel open:

    VM 127.0.0.1:8899  ==ssh -R==>  host 127.0.0.1:8899

Every request must carry the shared token from token.txt.

Endpoints
    GET  /health                        -> {"ok": true, ...}
    POST /run   (JSON body)             -> run a shell command on this host
    POST /put?path=<absolute path>      -> raw request body becomes that file
    GET  /get?path=<absolute path>      -> raw file bytes
    GET  /ls?path=<absolute path>       -> JSON directory listing

Requires Python 3.8+ and nothing outside the standard library.
"""

from __future__ import annotations

import hmac
import json
import os
import platform
import socket
import subprocess
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = "127.0.0.1"
PORT = int(os.environ.get("BRIDGE_PORT", "8899"))
BASE_DIR = Path(__file__).resolve().parent
TOKEN_FILE = BASE_DIR / "token.txt"
MAX_BODY = 8 * 1024 ** 3  # 8 GiB hard ceiling for one upload
OUTPUT_CAP = 4 * 1024 * 1024  # bytes of stdout/stderr kept per run


def load_token() -> str:
    """Return the shared token, creating it on first launch."""
    if TOKEN_FILE.exists():
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = os.urandom(24).hex()
    TOKEN_FILE.write_text(token + "\n", encoding="utf-8")
    try:
        os.chmod(TOKEN_FILE, 0o600)
    except OSError:
        pass
    return token


TOKEN = load_token()


def log(message: str) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"{stamp} {message}"
    print(line, flush=True)
    try:
        with open(BASE_DIR / "bridge.log", "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def run_command(payload: dict) -> dict:
    """Execute one command on this host and collect its output."""
    cmd = payload.get("cmd")
    if not isinstance(cmd, str) or not cmd.strip():
        return {"error": "cmd must be a non-empty string"}
    shell = (payload.get("shell") or "powershell").lower()
    cwd = payload.get("cwd") or None
    timeout = payload.get("timeout")
    timeout = float(timeout) if isinstance(timeout, (int, float)) and timeout > 0 else 0.0

    if shell == "cmd":
        argv = ["cmd.exe", "/d", "/c", cmd]
    else:
        prelude = (
            "$ProgressPreference='SilentlyContinue';"
            "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
            "$OutputEncoding=[System.Text.Encoding]::UTF8;"
        )
        argv = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            prelude + cmd,
        ]

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["BRIDGE"] = "1"

    started = time.time()
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout or None,
        )
    except subprocess.TimeoutExpired as exc:
        elapsed = int((time.time() - started) * 1000)
        return {
            "exit": -1,
            "timedOut": True,
            "durationMs": elapsed,
            "stdout": (exc.stdout or b"")[:OUTPUT_CAP].decode("utf-8", "replace"),
            "stderr": (exc.stderr or b"")[:OUTPUT_CAP].decode("utf-8", "replace"),
        }
    except FileNotFoundError as exc:
        return {"exit": -1, "stderr": f"command not found: {exc}", "stdout": ""}
    except Exception as exc:  # noqa: BLE001 - surface anything to the caller
        return {"exit": -1, "stderr": f"{type(exc).__name__}: {exc}", "stdout": ""}

    elapsed = int((time.time() - started) * 1000)
    return {
        "exit": completed.returncode,
        "durationMs": elapsed,
        "cwd": cwd or os.getcwd(),
        "stdout": completed.stdout[:OUTPUT_CAP].decode("utf-8", "replace"),
        "stderr": completed.stderr[:OUTPUT_CAP].decode("utf-8", "replace"),
    }


class BridgeHandler(BaseHTTPRequestHandler):
    server_version = "DSHBridge/1"
    protocol_version = "HTTP/1.1"

    # -- helpers ---------------------------------------------------------
    def authorised(self) -> bool:
        supplied = self.headers.get("X-Bridge-Token", "")
        return hmac.compare_digest(supplied, TOKEN)

    def send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def query_path(self) -> Path | None:
        parsed = urllib.parse.urlparse(self.path)
        values = urllib.parse.parse_qs(parsed.query)
        raw = (values.get("path") or [""])[0]
        if not raw:
            return None
        return Path(raw)

    def read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return b""
        return self.rfile.read(length)

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - stdlib name
        log(f"{self.address_string()} {fmt % args}")

    # -- routes ----------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        if not self.authorised():
            self.send_json(401, {"error": "bad or missing X-Bridge-Token"})
            return
        route = urllib.parse.urlparse(self.path).path

        if route == "/health":
            self.send_json(
                200,
                {
                    "ok": True,
                    "host": socket.gethostname(),
                    "user": os.environ.get("USERNAME", ""),
                    "platform": platform.platform(),
                    "python": platform.python_version(),
                    "home": str(Path.home()),
                    "uptimeSeconds": int(time.monotonic()),
                },
            )
            return

        if route == "/ls":
            target = self.query_path()
            if target is None:
                self.send_json(400, {"error": "path query parameter required"})
                return
            if not target.exists():
                self.send_json(404, {"error": f"not found: {target}"})
                return
            entries = []
            try:
                for item in os.scandir(target):
                    try:
                        info = {
                            "name": item.name,
                            "dir": item.is_dir(),
                            "size": item.stat().st_size if not item.is_dir() else 0,
                            "mtime": int(item.stat().st_mtime),
                        }
                    except OSError:
                        continue
                    entries.append(info)
            except OSError as exc:
                self.send_json(400, {"error": f"{type(exc).__name__}: {exc}"})
                return
            self.send_json(200, {"path": str(target), "entries": entries})
            return

        if route == "/get":
            target = self.query_path()
            if target is None:
                self.send_json(400, {"error": "path query parameter required"})
                return
            if not target.is_file():
                self.send_json(404, {"error": f"not a file: {target}"})
                return
            size = target.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(size))
            self.send_header("X-Bridge-Path", urllib.parse.quote(str(target)))
            self.end_headers()
            with open(target, "rb") as handle:
                while chunk := handle.read(1024 * 256):
                    self.wfile.write(chunk)
            return

        self.send_json(404, {"error": f"unknown route {route}"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        if not self.authorised():
            self.send_json(401, {"error": "bad or missing X-Bridge-Token"})
            return
        route = urllib.parse.urlparse(self.path).path

        if route == "/run":
            try:
                payload = json.loads(self.read_body() or b"{}")
            except json.JSONDecodeError as exc:
                self.send_json(400, {"error": f"invalid JSON: {exc}"})
                return
            result = run_command(payload)
            self.send_json(200, result)
            return

        if route == "/put":
            target = self.query_path()
            if target is None:
                self.send_json(400, {"error": "path query parameter required"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                self.send_json(413, {"error": f"body over {MAX_BODY} bytes"})
                return
            target.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            with open(target, "wb") as handle:
                remaining = length
                while remaining > 0:
                    chunk = self.rfile.read(min(1024 * 256, remaining))
                    if not chunk:
                        break
                    handle.write(chunk)
                    written += len(chunk)
                    remaining -= len(chunk)
            self.send_json(200, {"ok": True, "path": str(target), "bytes": written})
            return

        self.send_json(404, {"error": f"unknown route {route}"})


def main() -> int:
    server = ThreadingHTTPServer((HOST, PORT), BridgeHandler)
    log(f"bridge listening on http://{HOST}:{PORT} (token file: {TOKEN_FILE})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("bridge stopping (KeyboardInterrupt)")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

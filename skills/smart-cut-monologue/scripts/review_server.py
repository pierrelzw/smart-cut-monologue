#!/usr/bin/env python3
"""
Review server for smart-cut-monologue.

Serves an interactive HTML page that lets the user confirm which suggested
cuts to apply. On POST /confirm, writes cuts.json into the workdir and
shuts down cleanly.

Usage: python3 review_server.py <workdir>
Exit 0 on confirm, 2 on user abort (SIGINT), 3 on timeout.
"""

import http.server
import json
import os
import socket
import socketserver
import sys
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
REVIEW_HTML = SKILL_DIR / "assets" / "review.html"

if len(sys.argv) != 2:
    print("usage: review_server.py <workdir>", file=sys.stderr)
    sys.exit(1)

workdir = Path(sys.argv[1]).resolve()
manifest = json.loads((workdir / "manifest.json").read_text())
transcript = json.loads((workdir / "transcript.json").read_text())
silence = json.loads((workdir / "silence.json").read_text())
suggestions = json.loads((workdir / "suggestions.json").read_text())
video_path = Path(manifest["video"])

if not video_path.is_file():
    print(f"review: video not found: {video_path}", file=sys.stderr)
    sys.exit(1)

confirmed = {"done": False, "cuts": None}
server_ref = {"srv": None}


def pick_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # quiet

    def _send(self, status: int, body: bytes, content_type: str):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str):
        data = path.read_bytes()
        self._send(200, data, content_type)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        route = url.path
        if route == "/" or route == "/review.html":
            self._send_file(REVIEW_HTML, "text/html; charset=utf-8")
        elif route == "/data.json":
            payload = {
                "manifest": manifest,
                "transcript": transcript,
                "silence": silence,
                "suggestions": suggestions,
            }
            self._send(200, json.dumps(payload).encode(), "application/json")
        elif route == "/video":
            # Stream video with Range support (HTML5 seeking)
            self._serve_video()
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        if url.path != "/confirm":
            self._send(404, b"not found", "text/plain")
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw)
            cuts = body.get("cuts", [])
            if not isinstance(cuts, list):
                raise ValueError("cuts must be a list")
            for c in cuts:
                float(c["start"]); float(c["end"])
        except Exception as e:
            self._send(400, f"bad payload: {e}".encode(), "text/plain")
            return

        out = {
            "video": str(video_path),
            "duration": manifest["duration"],
            "cuts": cuts,
            "confirmed_at": time.time(),
        }
        (workdir / "cuts.json").write_text(json.dumps(out, indent=2))
        confirmed["done"] = True
        confirmed["cuts"] = cuts
        self._send(200, b'{"ok":true}', "application/json")
        threading.Thread(target=server_ref["srv"].shutdown, daemon=True).start()

    def _serve_video(self):
        size = video_path.stat().st_size
        rng = self.headers.get("Range")
        f = video_path.open("rb")
        try:
            ctype = "video/mp4"
            if rng and rng.startswith("bytes="):
                start_s, _, end_s = rng[6:].partition("-")
                start = int(start_s) if start_s else 0
                end = int(end_s) if end_s else size - 1
                end = min(end, size - 1)
                length = end - start + 1
                self.send_response(206)
                self.send_header("Content-Type", ctype)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.send_header("Content-Length", str(length))
                self.end_headers()
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(64 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            else:
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(size))
                self.end_headers()
                while True:
                    chunk = f.read(64 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            f.close()


def main():
    port = pick_port()
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", port), Handler)
    server_ref["srv"] = srv
    url = f"http://127.0.0.1:{port}/review.html"
    print(f"review: serving at {url}")
    print("review: waiting for you to click 'Confirm & Cut' in the browser...")
    try:
        webbrowser.open(url)
    except Exception:
        print(f"review: open this URL manually → {url}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("review: aborted by user", file=sys.stderr)
        sys.exit(2)
    finally:
        srv.server_close()

    if not confirmed["done"]:
        print("review: server stopped without confirmation", file=sys.stderr)
        sys.exit(2)
    print(f"review: confirmed {len(confirmed['cuts'])} cuts → {workdir / 'cuts.json'}")


if __name__ == "__main__":
    main()

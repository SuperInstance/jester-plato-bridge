#!/usr/bin/env python3
"""jester-plato-bridge — Translates court-jester tile submissions to PLATO knowledge.

Listens on port 4050 for requests in court-jester's PLATO bridge format,
translates them to our PLATO server's /submit format, and forwards them.

Endpoints:
  POST /api/rooms/{room}/tiles   — court-jester native format
  GET  /api/rooms/{room}/tiles   — read tiles back (court-jester compat)
  GET  /api/rooms                — list rooms (court-jester compat)
  GET  /api/health               — court-jester health check
  POST /tile                     — simpler shorthand endpoint
  GET  /health                   — extended health check

Usage:
  python3 bridge.py [--port PORT] [--plato-url URL] [--plato-local URL]
"""

import http.server
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PLATO_REMOTE = os.environ.get(
    "PLATO_REMOTE_URL", "http://147.224.38.131:8847"
)
PLATO_LOCAL = os.environ.get(
    "PLATO_LOCAL_URL", "http://localhost:8847"
)
DEFAULT_PORT = int(os.environ.get("BRIDGE_PORT", "4050"))
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

# ---------------------------------------------------------------------------
# Counter
# ---------------------------------------------------------------------------

_tiles_forwarded = 0

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    level=getattr(logging, LOG_LEVEL, logging.INFO),
)
log = logging.getLogger("jester-bridge")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def room_to_domain(room: str) -> str:
    """Convert 'jester/ideation' → 'jester-ideation'."""
    return re.sub(r"[^a-zA-Z0-9_-]", "-", room).strip("-").lower()


def ensure_20_chars(text: str) -> str:
    """Pad text to at least 20 characters (PLATO requirement)."""
    if len(text) >= 20:
        return text
    return text + " " + "x" * (20 - len(text) - 1)


def forward_to_plato(domain: str, question: str, answer: str,
                     source: str = "court-jester",
                     confidence: float = 0.8,
                     tags: list = None) -> dict:
    """Forward a tile to PLATO's /submit endpoint.

    Posts to both remote and local PLATO instances.
    Returns the response from the first successful remote submission,
    or falls back to local.
    """
    global _tiles_forwarded

    if tags is None:
        tags = []

    # Ensure at least 20 chars for answer
    answer = ensure_20_chars(answer)

    payload = {
        "domain": domain,
        "question": question,
        "answer": answer,
        "source": source,
        "confidence": confidence,
        "tags": list(tags),
    }

    body_bytes = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}

    last_error = None

    # Try remote PLATO first
    for label, base_url in [("remote", PLATO_REMOTE), ("local", PLATO_LOCAL)]:
        submit_url = f"{base_url.rstrip('/')}/submit"
        try:
            req = urllib.request.Request(
                submit_url, data=body_bytes, headers=headers, method="POST"
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp_body = resp.read().decode("utf-8")
                result = json.loads(resp_body) if resp_body.strip() else {}
            _tiles_forwarded += 1
            log.info("Forwarded tile %s/%s → %s (via %s)",
                     domain, question[:40], submit_url, label)
            return result
        except (urllib.error.URLError, urllib.error.HTTPError,
                OSError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            log.warning("PLATO %s submit failed: %s", label, last_error)
            # Also try the /room/{room}/submit endpoint as fallback
            try:
                alt_url = f"{base_url.rstrip('/')}/room/{domain}/submit"
                req = urllib.request.Request(
                    alt_url, data=body_bytes, headers=headers, method="POST"
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    resp_body = resp.read().decode("utf-8")
                    result = json.loads(resp_body) if resp_body.strip() else {}
                _tiles_forwarded += 1
                log.info("Forwarded tile %s/%s → %s (via %s alt)",
                         domain, question[:40], alt_url, label)
                return result
            except (urllib.error.URLError, urllib.error.HTTPError,
                    OSError, json.JSONDecodeError) as alt_exc:
                log.warning("PLATO %s alt submit also failed: %s",
                            label, str(alt_exc))

    log.error("All PLATO endpoints failed — last error: %s", last_error)
    return {"error": f"All PLATO endpoints failed: {last_error}"}


def read_tiles_from_plato(room: str, limit: int = 5) -> list:
    """Read tiles from PLATO's GET /room/{room}/tiles endpoint."""
    for label, base_url in [("remote", PLATO_REMOTE), ("local", PLATO_LOCAL)]:
        url = f"{base_url.rstrip('/')}/room/{urllib.parse.quote(room, safe='')}/tiles"
        if limit:
            url += f"?limit={limit}"
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            # Normalize to court-jester tile format
            tiles = data.get("tiles", data if isinstance(data, list) else [])
            normalized = []
            for t in tiles:
                normalized.append({
                    "title": t.get("question", t.get("title", "")),
                    "content": t.get("answer", t.get("content", "")),
                    "tags": t.get("tags", []),
                    "timestamp": t.get("timestamp",
                                       t.get("created_at",
                                             time.strftime(
                                                 "%Y-%m-%dT%H:%M:%SZ",
                                                 time.gmtime()))),
                })
            log.info("Read %d tiles from %s (via %s)",
                     len(normalized), url, label)
            return normalized
        except (urllib.error.URLError, urllib.error.HTTPError,
                OSError, json.JSONDecodeError) as exc:
            log.warning("PLATO %s read failed: %s", label, str(exc))

    return []


def list_rooms_from_plato(prefix: str = "jester") -> list:
    """List rooms from PLATO's room listing endpoint."""
    for label, base_url in [("local", PLATO_LOCAL), ("remote", PLATO_REMOTE)]:
        urls_to_try = [
            f"{base_url.rstrip('/')}/rooms",
            f"{base_url.rstrip('/')}/api/rooms",
        ]
        for url in urls_to_try:
            try:
                if prefix:
                    sep = "&" if "?" in url else "?"
                    url_with_prefix = f"{url}{sep}prefix={urllib.parse.quote(prefix, safe='')}"
                else:
                    url_with_prefix = url
                req = urllib.request.Request(url_with_prefix, method="GET")
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                rooms = data.get("rooms", data if isinstance(data, list) else [])
                log.info("Listed %d rooms from %s", len(rooms), url_with_prefix)
                return rooms
            except (urllib.error.URLError, urllib.error.HTTPError,
                    OSError, json.JSONDecodeError) as exc:
                log.warning("PLATO %s room list failed (%s): %s",
                            label, url, str(exc))

    return []


def check_plato_health() -> dict:
    """Check if PLATO server is reachable."""
    results = {}
    for label, base_url in [("remote", PLATO_REMOTE), ("local", PLATO_LOCAL)]:
        try:
            url = f"{base_url.rstrip('/')}/health"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                results[label] = resp.status == 200
        except Exception:
            results[label] = False
    return results


# ---------------------------------------------------------------------------
# HTTP Request Handler
# ---------------------------------------------------------------------------

class BridgeHandler(http.server.BaseHTTPRequestHandler):

    # Silence default logging (we do our own)
    def log_message(self, format, *args):
        log.debug("HTTP %s", format % args)

    def _send_json(self, status: int, data):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            log.warning("Invalid JSON body: %s", exc)
            return {}

    # ---- Routing -------------------------------------------------------

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        params = urllib.parse.parse_qs(parsed.query)

        log.info("GET %s", self.path)

        # GET /health
        if path == "/health":
            plato = check_plato_health()
            self._send_json(200, {
                "status": "ok",
                "plato": "connected" if any(plato.values()) else "disconnected",
                "plato_details": plato,
                "tiles_forwarded": _tiles_forwarded,
            })
            return

        # GET /api/health
        if path == "/api/health":
            plato = check_plato_health()
            self._send_json(200, {
                "alive": any(plato.values()),
                "error": None if any(plato.values()) else "PLATO unreachable",
            })
            return

        # GET /api/rooms — list rooms
        if path == "/api/rooms" or path == "":
            prefix = params.get("prefix", ["jester"])[0]
            rooms = list_rooms_from_plato(prefix)
            self._send_json(200, {"rooms": rooms})
            return

        # GET /api/rooms/{room}/tiles — read tiles
        room_match = re.match(r"^/api/rooms/([^/]+)/tiles$", path)
        if room_match:
            room = urllib.parse.unquote(room_match.group(1))
            limit = int(params.get("limit", ["5"])[0])
            tiles = read_tiles_from_plato(room, limit)
            self._send_json(200, {"tiles": tiles})
            return

        # Fallback
        self._send_json(404, {"error": f"Not found: {self.path}"})

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        body = self._read_body()
        log.info("POST %s body=%s", self.path,
                 json.dumps(body, ensure_ascii=False)[:200])

        # POST /api/rooms/{room}/tiles — court-jester native format
        room_match = re.match(r"^/api/rooms/([^/]+)/tiles$", path)
        if room_match:
            room = urllib.parse.unquote(room_match.group(1))
            title = body.get("title", "")
            content = body.get("content", "")
            tags = body.get("tags", [])
            domain = room_to_domain(room)
            all_tags = list(tags) + ["court-jester"]
            if "jester" not in all_tags:
                all_tags.append("jester")
            if domain.replace("-", "") not in "".join(all_tags).lower():
                all_tags.append(domain)

            result = forward_to_plato(
                domain=domain,
                question=title,
                answer=content,
                source="court-jester",
                confidence=0.8,
                tags=all_tags,
            )

            if "error" in result:
                self._send_json(502, {"success": False, "error": result["error"]})
            else:
                self._send_json(200, {"success": True, "plato_response": result})
            return

        # POST /tile — simpler shorthand (includes room field)
        if path == "/tile":
            room = body.get("room", "jester/general")
            title = body.get("title", body.get("question", ""))
            content = body.get("content", body.get("answer", ""))
            tags = body.get("tags", [])
            domain = room_to_domain(room)
            all_tags = list(tags) + ["court-jester"]
            if "jester" not in all_tags:
                all_tags.append("jester")
            if domain.replace("-", "") not in "".join(all_tags).lower():
                all_tags.append(domain)

            result = forward_to_plato(
                domain=domain,
                question=title,
                answer=content,
                source="court-jester",
                confidence=0.8,
                tags=all_tags,
            )

            if "error" in result:
                self._send_json(502, {"success": False, "error": result["error"]})
            else:
                self._send_json(200, {"success": True, "plato_response": result})
            return

        # Fallback
        self._send_json(404, {"error": f"Not found: {self.path}"})


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------

def main():
    import argparse

    global PLATO_REMOTE, PLATO_LOCAL

    parser = argparse.ArgumentParser(
        description="jester-plato-bridge — translate court-jester tiles to PLATO knowledge"
    )
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT,
        help=f"Listen port (default: {DEFAULT_PORT})"
    )
    parser.add_argument(
        "--plato-url", default=None,
        help=f"PLATO remote URL (default: {PLATO_REMOTE})"
    )
    parser.add_argument(
        "--plato-local", default=None,
        help=f"PLATO local URL (default: {PLATO_LOCAL})"
    )
    parser.add_argument(
        "--host", default="0.0.0.0",
        help="Bind address (default: 0.0.0.0)"
    )
    args = parser.parse_args()

    # Override from CLI args
    if args.plato_url:
        PLATO_REMOTE = args.plato_url
    if args.plato_local:
        PLATO_LOCAL = args.plato_local

    server = http.server.HTTPServer(
        (args.host, args.port), BridgeHandler
    )

    print(f"🔮 jester-plato-bridge listening on {args.host}:{args.port}")
    print(f"   Remote PLATO: {PLATO_REMOTE}")
    print(f"   Local PLATO:  {PLATO_LOCAL}")
    print(f"   Tiles forwarded: {_tiles_forwarded}")
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.server_close()


if __name__ == "__main__":
    main()

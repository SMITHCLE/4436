"""
routes.py - application logic: response building, routing, subscribe + token checks.

server.py owns the TRANSPORT side (sockets, TCP, threads, parsing bytes off the wire).
This module owns the HTTP/APPLICATION side: given an already-parsed request, decide
what to answer and build the raw response bytes.
"""

import html
import json
import os
import secrets
import threading
from datetime import datetime, timezone
from urllib.parse import parse_qs

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLIC_DIR = os.path.join(BASE_DIR, "public")
LOG_FILE = os.path.join(BASE_DIR, "subscribers.log")

# Status code -> reason phrase for the status line ("HTTP/1.1 404 Not Found").
REASONS = {
    200: "OK",
    400: "Bad Request",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    413: "Payload Too Large",
    500: "Internal Server Error",
}

# File extension -> Content-Type header, so the browser knows how to render the bytes.
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".txt": "text/plain; charset=utf-8",
}

# Pages that need a valid token. Listed by their *final* path so that neither
# "/deals" nor "/deals.html" can be used to sneak past the check.
PROTECTED_PATHS = {"/deals", "/deals.html"}

# Many threads may write the log at once; the lock stops lines interleaving.
_log_lock = threading.Lock()


# --------------------------------------------------------------------------
# Building raw HTTP responses
# --------------------------------------------------------------------------
def build_response(status, body, content_type="text/html; charset=utf-8", extra_headers=None):
    """Assemble: status line + headers + blank line + body, as raw bytes.
    This is exactly what travels over the TCP connection back to the client."""
    if isinstance(body, str):
        body = body.encode("utf-8")
    headers = {
        "Content-Type": content_type,
        "Content-Length": str(len(body)),  # tells the client where the body ends
        "Connection": "close",             # one request per TCP connection (simple)
        "Server": "SocketServer/1.0",
    }
    if extra_headers:
        headers.update(extra_headers)
    head = f"HTTP/1.1 {status} {REASONS[status]}\r\n"
    head += "".join(f"{k}: {v}\r\n" for k, v in headers.items())
    return head.encode("iso-8859-1") + b"\r\n" + body


def error_page(status, fallback_text):
    """Send public/<status>.html if it exists (custom 404 / 403 pages)."""
    try:
        with open(os.path.join(PUBLIC_DIR, f"{status}.html"), "rb") as f:
            body = f.read()
    except OSError:
        body = f"<h1>{status} {fallback_text}</h1>".encode()
    return build_response(status, body)


# --------------------------------------------------------------------------
# Subscriber storage: subscribers.log, one JSON object per line
# --------------------------------------------------------------------------
def log_subscriber(name, email, token):
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "name": name,
        "email": email,
        "token": token,
    }
    # JSON-per-line means a name containing a newline or tab can't corrupt the file.
    with _log_lock:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")


def is_valid_token(token):
    """A token is valid if it appears in subscribers.log (so it survives restarts)."""
    if not token:
        return False
    try:
        with _log_lock, open(LOG_FILE, encoding="utf-8") as f:
            for line in f:
                try:
                    if secrets.compare_digest(json.loads(line)["token"], token):
                        return True
                except (ValueError, KeyError, TypeError):
                    continue  # skip a damaged line
    except FileNotFoundError:
        pass
    return False


# --------------------------------------------------------------------------
# Route handlers
# --------------------------------------------------------------------------
def serve_static(path):
    if path == "/":
        path = "/index.html"
    if "." not in os.path.basename(path):   # pretty URL: /deals -> /deals.html
        path += ".html"

    # SECURITY: resolve to a real path and require it to stay inside public/,
    # otherwise "GET /../server.py" would read arbitrary files (path traversal).
    if "\x00" in path:                      # null bytes can't be in a filename
        return error_page(404, "Not Found")
    root = os.path.realpath(PUBLIC_DIR)
    full = os.path.realpath(os.path.join(PUBLIC_DIR, path.lstrip("/")))
    if os.path.commonpath([full, root]) != root or not os.path.isfile(full):
        return error_page(404, "Not Found")

    ext = os.path.splitext(full)[1].lower()
    with open(full, "rb") as f:
        return build_response(200, f.read(), CONTENT_TYPES.get(ext, "application/octet-stream"))


def serve_deals(path, query):
    """Subscriber-only page: 403 Forbidden unless ?token= matches a logged token."""
    token = parse_qs(query).get("token", [""])[0]
    if not is_valid_token(token):
        return error_page(403, "Forbidden")
    return serve_static(path)


def handle_subscribe(body):
    """POST /subscribe  (application/x-www-form-urlencoded: name=...&email=...)"""
    form = parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
    name = form.get("name", [""])[0].strip()
    email = form.get("email", [""])[0].strip()
    if not name or "@" not in email or len(name) > 100 or len(email) > 200:
        return build_response(400, "<h1>400 Bad Request</h1><p>Please give a name and a valid email.</p>"
                                   "<p><a href='/'>Back</a></p>")

    token = secrets.token_urlsafe(16)        # cryptographically random, URL-safe
    log_subscriber(name, email, token)

    link = f"/deals?token={token}"
    page = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Subscribed!</title>
<link rel="stylesheet" href="/style.css"></head>
<body>
  <h1>Thanks, {html.escape(name)}!</h1>
  <p>You're subscribed as <strong>{html.escape(email)}</strong>.</p>
  <p><a id="deals-link" href="{link}">View your subscriber-only deals</a></p>
  <p><small>Your link: <code>{link}</code></small></p>
</body></html>"""
    return build_response(200, page)


# --------------------------------------------------------------------------
# Dispatcher: method + path -> handler
# --------------------------------------------------------------------------
def handle_request(method, path, query, headers, body):
    if path == "/subscribe":
        if method == "POST":
            return handle_subscribe(body)
        return build_response(405, "<h1>405 Method Not Allowed</h1>", extra_headers={"Allow": "POST"})

    if method != "GET":
        return build_response(405, "<h1>405 Method Not Allowed</h1>", extra_headers={"Allow": "GET"})

    if path in PROTECTED_PATHS:
        return serve_deals(path, query)
    return serve_static(path)

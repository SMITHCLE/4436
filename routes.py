"""
routes.py - application logic: response building, routing, subscribe/login, token checks.

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

# Customer tiers, decided by how many times an email has visited (subscribe + logins).
RETURNING_AT = 2   # 2nd visit onward  -> "returning" customer
FREQUENT_AT = 4    # 4th visit onward  -> "frequent" customer

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
# Visit storage: subscribers.log, one JSON object per line.
# Every subscribe AND every login appends a line with a NEW token, so the log
# doubles as the record of each customer's visits (timestamp + session token).
# --------------------------------------------------------------------------
def log_visit(name, email, token, event):
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "event": event,                 # "subscribe" or "login"
        "name": name,
        "email": email,
        "token": token,
    }
    # JSON-per-line means a name containing a newline or tab can't corrupt the file.
    with _log_lock:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")


def read_entries():
    """Return every well-formed log entry (oldest first)."""
    entries = []
    with _log_lock:
        try:
            with open(LOG_FILE, encoding="utf-8") as f:
                for line in f:
                    try:
                        e = json.loads(line)
                        if isinstance(e, dict) and "token" in e and "email" in e:
                            entries.append(e)
                    except ValueError:
                        continue            # skip a damaged line
        except FileNotFoundError:
            pass
    return entries


def find_by_token(token):
    """Return the log entry that issued this token, or None."""
    if not token:
        return None
    for e in read_entries():
        if secrets.compare_digest(str(e["token"]), token):
            return e
    return None


def visits_for(email):
    email = email.lower()
    return sum(1 for e in read_entries() if str(e["email"]).lower() == email)


def tier_for(visits):
    if visits >= FREQUENT_AT:
        return "frequent"
    if visits >= RETURNING_AT:
        return "returning"
    return "new"


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


def tier_block(name, visits):
    """The personalised banner + bonus shown at the top of the deals page."""
    tier = tier_for(visits)
    who = html.escape(name)
    if tier == "frequent":
        label, msg = "Frequent customer", (
            f"Welcome back, {who}! Visit #{visits}. You're one of our regulars, so you get "
            "<strong>15% off your whole order</strong> and a free cupcake with any cake.")
    elif tier == "returning":
        label, msg = "Returning customer", (
            f"Welcome back, {who}! Visit #{visits}. Returning-customer bonus: "
            "<strong>a free coffee with any purchase</strong>.")
    else:
        label, msg = "New customer", (
            f"Welcome, {who}! This is your first visit. Come back {FREQUENT_AT - 1} more times "
            "to unlock our frequent-customer rewards.")
    return f'<div class="tier {tier}"><span class="badge-tier">{label}</span><p>{msg}</p></div>'


def serve_deals(path, query):
    """Subscriber-only page: 403 Forbidden unless ?token= matches a logged token."""
    token = parse_qs(query).get("token", [""])[0]
    entry = find_by_token(token)
    if entry is None:
        return error_page(403, "Forbidden")
    with open(os.path.join(PUBLIC_DIR, "deals.html"), encoding="utf-8") as f:
        page = f.read()
    banner = tier_block(str(entry.get("name", "")), visits_for(str(entry["email"])))
    return build_response(200, page.replace("<!--TIER-->", banner))


def parse_form(body):
    """Parse an application/x-www-form-urlencoded body into a plain dict."""
    form = parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
    return {k: v[0] for k, v in form.items()}


def link_page(title, heading_html, extra_html, link):
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>{title}</title>
<link rel="stylesheet" href="/style.css"></head>
<body>
  <h1>{heading_html}</h1>
  {extra_html}
  <p><a id="deals-link" href="{link}">View your subscriber-only deals</a></p>
  <p><small>Your link: <code>{link}</code></small></p>
</body></html>"""


def handle_subscribe(body):
    """POST /subscribe  (application/x-www-form-urlencoded: name=...&email=...)"""
    form = parse_form(body)
    name = form.get("name", "").strip()
    email = form.get("email", "").strip()
    if not name or "@" not in email or len(name) > 100 or len(email) > 200:
        return build_response(400, "<h1>400 Bad Request</h1><p>Please give a name and a valid email.</p>"
                                   "<p><a href='/'>Back</a></p>")

    token = secrets.token_urlsafe(16)        # cryptographically random, URL-safe
    log_visit(name, email, token, "subscribe")

    link = f"/deals?token={token}"
    return build_response(200, link_page(
        "Subscribed!", f"Thanks, {html.escape(name)}!",
        f"<p>You're subscribed as <strong>{html.escape(email)}</strong>.</p>", link))


def handle_login(body):
    """POST /login (email=...): returning customers get a NEW session token.
    The visit is logged, and the visit count decides new / returning / frequent."""
    email = parse_form(body).get("email", "").strip()
    previous = [e for e in read_entries() if str(e["email"]).lower() == email.lower()] if email else []
    if not previous:
        # Friendly refusal (403): they must subscribe first.
        return build_response(403, """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Not subscribed</title>
<link rel="stylesheet" href="/style.css"></head>
<body>
  <h1>We couldn't find that email</h1>
  <p>Rewards are for subscribers. <a href="/#subscribe">Subscribe here</a> first, then come back to log in.</p>
</body></html>""")

    name = str(previous[0].get("name", ""))
    token = secrets.token_urlsafe(16)        # a fresh token for every session/visit
    log_visit(name, email, token, "login")
    visits = len(previous) + 1
    label = {"new": "new", "returning": "returning", "frequent": "frequent"}[tier_for(visits)]

    link = f"/deals?token={token}"
    return build_response(200, link_page(
        "Welcome back!", f"Welcome back, {html.escape(name)}!",
        f"<p>This is visit <strong>#{visits}</strong>, so you're a <strong>{label}</strong> customer.</p>", link))


# --------------------------------------------------------------------------
# Dispatcher: method + path -> handler
# --------------------------------------------------------------------------
POST_ROUTES = {"/subscribe": handle_subscribe, "/login": handle_login}
GET_AND_POST = {"/login"}          # /login also has a GET page (the login form)


def handle_request(method, path, query, headers, body):
    if path in POST_ROUTES:
        if method == "POST":
            return POST_ROUTES[path](body)
        if path not in GET_AND_POST:
            return build_response(405, "<h1>405 Method Not Allowed</h1>", extra_headers={"Allow": "POST"})

    if method != "GET":
        return build_response(405, "<h1>405 Method Not Allowed</h1>", extra_headers={"Allow": "GET"})

    if path in PROTECTED_PATHS:
        return serve_deals(path, query)
    return serve_static(path)

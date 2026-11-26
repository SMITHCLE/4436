#!/usr/bin/env python3
"""
Minimal HTTP/1.1 server built directly on TCP sockets (standard library only).

STAGE 1: GET + 404 only. Single-threaded. POST, token/403 and threading come later.

How this maps to networking concepts
------------------------------------
* HTTP is an APPLICATION-layer protocol. It is just structured text that
  travels inside a TCP byte stream; TCP (transport layer) gives us a reliable,
  ordered stream, and the socket API is how a program talks to TCP.
* Server socket lifecycle:  socket() -> bind() -> listen() -> accept() loop.
    - socket(AF_INET, SOCK_STREAM): IPv4 + TCP.
    - bind((host, port)): attach the socket to a local IP address and port.
    - listen(): mark it as a *listening* socket; the OS completes the TCP
      3-way handshake (SYN, SYN-ACK, ACK) and queues connections.
    - accept(): take one finished connection off the queue. It returns a NEW
      socket dedicated to that client (the listening socket stays open).
* An HTTP/1.1 request looks like:
      GET /index.html HTTP/1.1\r\n          <- request line
      Host: localhost:8080\r\n              <- headers, one per line
      \r\n                                  <- blank line ends the headers
      [optional body]
  A response is: status line, headers, blank line, body.
* TCP is a byte stream with no message boundaries, so we must keep calling
  recv() until we have seen the blank line (b"\r\n\r\n") that ends the headers.
"""

import os
import socket
import sys
from urllib.parse import urlsplit, unquote

HOST = "0.0.0.0"
PORT = 8080
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLIC_DIR = os.path.join(BASE_DIR, "public")
MAX_HEADER_BYTES = 16 * 1024  # refuse absurdly large headers

# Status codes -> reason phrases used in the status line ("HTTP/1.1 404 Not Found").
REASONS = {
    200: "OK",
    400: "Bad Request",
    404: "Not Found",
    405: "Method Not Allowed",
}

# File extension -> Content-Type header, so the browser knows how to render bytes.
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".txt": "text/plain; charset=utf-8",
}


# --------------------------------------------------------------------------
# Request parsing (application layer: turn raw bytes into something usable)
# --------------------------------------------------------------------------
def read_request(conn):
    """Read from the TCP stream until the end-of-headers marker; return raw bytes
    (headers plus whatever part of the body arrived with them), or None if the
    client closed before sending a full request."""
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = conn.recv(4096)          # blocks until bytes arrive
        if not chunk:                    # empty bytes == peer closed the connection
            return None
        data += chunk
        if len(data) > MAX_HEADER_BYTES:
            return None
    return data


def parse_request(raw):
    """Parse raw request bytes into (method, path, query_string, version, headers).
    Raises ValueError for malformed requests (-> 400 Bad Request)."""
    head, _, _body = raw.partition(b"\r\n\r\n")
    lines = head.decode("iso-8859-1").split("\r\n")

    # Request line: METHOD SP TARGET SP VERSION
    parts = lines[0].split(" ")
    if len(parts) != 3:
        raise ValueError("bad request line")
    method, target, version = parts

    # Split "/deals?token=abc" into path "/deals" and query "token=abc".
    url = urlsplit(target)
    path = unquote(url.path)

    # Header lines: "Name: value". Header names are case-insensitive -> lowercase.
    headers = {}
    for line in lines[1:]:
        name, sep, value = line.partition(":")
        if sep:
            headers[name.strip().lower()] = value.strip()

    return method, path, url.query, version, headers


# --------------------------------------------------------------------------
# Response building (we assemble the raw HTTP text ourselves)
# --------------------------------------------------------------------------
def build_response(status, body, content_type="text/html; charset=utf-8", extra_headers=None):
    """Return the full raw HTTP response as bytes."""
    status_line = f"HTTP/1.1 {status} {REASONS[status]}\r\n"
    headers = {
        "Content-Type": content_type,
        "Content-Length": str(len(body)),   # tells the client where the body ends
        "Connection": "close",              # one request per TCP connection (simple)
        "Server": "SocketServer/0.1",
    }
    if extra_headers:
        headers.update(extra_headers)
    header_text = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
    return status_line.encode("iso-8859-1") + header_text.encode("iso-8859-1") + b"\r\n" + body


def not_found():
    """Custom 404 page (public/404.html), with a plain fallback if it's missing."""
    try:
        with open(os.path.join(PUBLIC_DIR, "404.html"), "rb") as f:
            body = f.read()
    except OSError:
        body = b"<h1>404 Not Found</h1>"
    return build_response(404, body)


# --------------------------------------------------------------------------
# Routing: map a URL path to a file under public/
# --------------------------------------------------------------------------
def serve_static(path):
    if path == "/":
        path = "/index.html"
    # Pretty URL: "/deals" -> "/deals.html"
    if "." not in os.path.basename(path):
        path += ".html"

    # SECURITY: resolve the real path and make sure it stays inside public/,
    # otherwise "GET /../server.py" could read arbitrary files (path traversal).
    full = os.path.realpath(os.path.join(PUBLIC_DIR, path.lstrip("/")))
    if os.path.commonpath([full, os.path.realpath(PUBLIC_DIR)]) != os.path.realpath(PUBLIC_DIR):
        return not_found()
    if not os.path.isfile(full):
        return not_found()

    ext = os.path.splitext(full)[1].lower()
    content_type = CONTENT_TYPES.get(ext, "application/octet-stream")
    with open(full, "rb") as f:
        return build_response(200, f.read(), content_type)


def handle_request(method, path, query, headers):
    """Dispatch on method + path. Stage 1: GET only."""
    if method != "GET":
        return build_response(405, b"<h1>405 Method Not Allowed</h1>", extra_headers={"Allow": "GET"})
    return serve_static(path)


# --------------------------------------------------------------------------
# Connection handling and main accept loop
# --------------------------------------------------------------------------
def handle_connection(conn, addr):
    try:
        raw = read_request(conn)
        if raw is None:
            return
        try:
            method, path, query, version, headers = parse_request(raw)
        except ValueError:
            conn.sendall(build_response(400, b"<h1>400 Bad Request</h1>"))
            return
        print(f"{addr[0]}:{addr[1]} {method} {path}" + (f"?{query}" if query else ""))
        conn.sendall(handle_request(method, path, query, headers))
    finally:
        conn.close()   # closing sends FIN: ends this TCP connection


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    # AF_INET = IPv4, SOCK_STREAM = TCP.
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # Allow quick restarts without "Address already in use" (TIME_WAIT).
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, port))
    server.listen(5)     # backlog: how many pending connections the OS may queue
    print(f"Listening on http://localhost:{port}  (Ctrl+C to stop)")
    try:
        while True:
            conn, addr = server.accept()   # blocks until a client connects
            handle_connection(conn, addr)  # Stage 1: handled inline, one at a time
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        server.close()


if __name__ == "__main__":
    main()

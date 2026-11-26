#!/usr/bin/env python3
"""
server.py - a small HTTP/1.1 web server built directly on TCP sockets.
Standard library only (no http.server, no frameworks).

How this file maps to networking concepts
-----------------------------------------
* HTTP is an APPLICATION-layer protocol: structured text carried inside a TCP
  byte stream. TCP (transport layer) gives reliable, ordered delivery; the
  socket API is how a program talks to TCP.
* Server socket lifecycle:  socket() -> bind() -> listen() -> accept() loop.
    - socket(AF_INET, SOCK_STREAM): IPv4 + TCP.
    - bind((host, port)): attach to a local IP address + port number.
    - listen(): mark it a *listening* socket; the OS performs the TCP 3-way
      handshake (SYN, SYN-ACK, ACK) and queues completed connections.
    - accept(): take one finished connection off the queue. It returns a NEW
      socket for that one client; the listening socket stays open for others.
* A request on the wire:
      POST /subscribe HTTP/1.1\r\n        <- request line (method, target, version)
      Host: localhost:8080\r\n            <- headers
      Content-Length: 27\r\n
      \r\n                                <- blank line = end of headers
      name=Ann&email=ann%40x.com          <- body (Content-Length bytes)
* TCP is a byte STREAM with no message boundaries, so recv() may return half a
  request or two at once. We loop until we see the blank line, then read exactly
  Content-Length more bytes for the body.
* Concurrency: accept() hands each client its own thread, so one slow client
  (blocked in recv) never stops other clients being served.
* Status codes tell the client the outcome: 200 OK, 400 Bad Request,
  403 Forbidden (understood, but not allowed), 404 Not Found, 405 Method Not Allowed.

Run:  python3 server.py [port]      (default 8080)
"""

import socket
import sys
import threading
from urllib.parse import urlsplit, unquote

import routes

HOST = "0.0.0.0"
PORT = 8080
MAX_HEADER_BYTES = 16 * 1024      # refuse absurdly large headers
MAX_BODY_BYTES = 64 * 1024        # refuse absurdly large bodies
CLIENT_TIMEOUT = 10               # seconds a client may stall before we drop it


class BadRequest(Exception):
    """Raised for malformed requests; becomes an HTTP 400 response."""


class TooLarge(Exception):
    """Raised for oversized requests; becomes an HTTP 413 response."""


# --------------------------------------------------------------------------
# Reading + parsing a request from the TCP byte stream
# --------------------------------------------------------------------------
def read_request(conn):
    """Return (head_bytes, body_bytes) or None if the client sent nothing."""
    data = b""
    while b"\r\n\r\n" not in data:               # keep reading until headers end
        chunk = conn.recv(4096)                  # blocks until bytes arrive
        if not chunk:                            # b"" means the peer closed
            if data:
                raise BadRequest("incomplete request")
            return None
        data += chunk
        if len(data) > MAX_HEADER_BYTES:
            raise TooLarge("headers too large")

    head, _, body = data.partition(b"\r\n\r\n")

    # The body (e.g. a POSTed form) length is announced in Content-Length.
    length = 0
    for line in head.decode("iso-8859-1").split("\r\n")[1:]:
        name, _, value = line.partition(":")
        if name.strip().lower() == "content-length":
            try:
                length = int(value.strip())
            except ValueError:
                raise BadRequest("bad Content-Length")
            if length < 0:
                raise BadRequest("bad Content-Length")
    if length > MAX_BODY_BYTES:
        raise TooLarge("body too large")

    while len(body) < length:                    # read the rest of the body
        chunk = conn.recv(min(4096, length - len(body)))
        if not chunk:
            raise BadRequest("body shorter than Content-Length")
        body += chunk
    return head, body[:length]


def parse_head(head):
    """Parse the request line + headers -> (method, path, query, version, headers)."""
    lines = head.decode("iso-8859-1").split("\r\n")
    parts = lines[0].split(" ")                  # METHOD SP TARGET SP VERSION
    if len(parts) != 3 or not parts[2].startswith("HTTP/"):
        raise BadRequest("bad request line")
    method, target, version = parts

    url = urlsplit(target)                       # "/deals?token=abc" -> path + query
    path = unquote(url.path)

    headers = {}
    for line in lines[1:]:
        name, sep, value = line.partition(":")
        if sep:
            headers[name.strip().lower()] = value.strip()   # names are case-insensitive
    return method, path, url.query, version, headers


# --------------------------------------------------------------------------
# One thread runs this per client connection
# --------------------------------------------------------------------------
def handle_connection(conn, addr):
    try:
        conn.settimeout(CLIENT_TIMEOUT)          # a stalled client can't hold the thread forever
        try:
            req = read_request(conn)
            if req is None:
                return
            head, body = req
            method, path, query, version, headers = parse_head(head)
        except BadRequest:
            conn.sendall(routes.build_response(400, "<h1>400 Bad Request</h1>"))
            return
        except TooLarge:
            conn.sendall(routes.build_response(413, "<h1>413 Payload Too Large</h1>"))
            return

        try:
            response = routes.handle_request(method, path, query, headers, body)
        except Exception as exc:                 # a bug in a handler must not leave the client hanging
            print(f"ERROR handling {method} {path}: {exc!r}", flush=True)
            response = routes.build_response(500, "<h1>500 Internal Server Error</h1>")
        status = response.split(b" ", 2)[1].decode()
        # Log the path only (not the query) so tokens don't leak into console logs.
        print(f"[{threading.current_thread().name}] {addr[0]}:{addr[1]} {method} {path} -> {status}", flush=True)
        conn.sendall(response)
    except (socket.timeout, ConnectionError, OSError):
        pass                                     # client vanished or stalled; just drop it
    finally:
        conn.close()                             # sends FIN: ends this TCP connection


# --------------------------------------------------------------------------
# Main accept loop
# --------------------------------------------------------------------------
def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)    # IPv4 + TCP
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # quick restarts (TIME_WAIT)
    server.bind((HOST, port))
    server.listen(50)                            # backlog of pending connections
    print(f"Listening on http://localhost:{port}  (Ctrl+C to stop)", flush=True)
    try:
        while True:
            conn, addr = server.accept()         # blocks until a client connects
            # One thread per connection: the main loop goes straight back to accept().
            threading.Thread(target=handle_connection, args=(conn, addr), daemon=True).start()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        server.close()


if __name__ == "__main__":
    main()

# Sunrise Bakery - socket-based HTTP/1.1 web server

A small business-promotions website served by a web server written from scratch
on raw TCP sockets. Python 3 standard library only (no Flask, Django, `http.server`, no pip installs).

## Run it

```bash
python3 server.py          # listens on port 8080
python3 server.py 9000     # or pick another port
```

Open http://localhost:8080 in a browser. Stop with Ctrl+C.

## Project layout

| Path | Purpose |
|---|---|
| `server.py` | Socket setup, accept loop, thread-per-connection, reading/parsing raw requests |
| `routes.py` | Response building, routing, `/subscribe`, token check, logging |
| `public/` | `index.html`, `deals.html`, `style.css`, `404.html`, `403.html`, `images/` |
| `subscribers.log` | Created at runtime. One JSON object per line: timestamp, name, email, token |

## Test with curl

1. **200 OK** - page that exists
   ```bash
   curl -i http://localhost:8080/
   ```
2. **404 Not Found** - custom page for a missing path
   ```bash
   curl -i http://localhost:8080/nope
   ```
3. **403 Forbidden** - protected page, no token, then a bad token (also try `/deals.html`; it is protected too)
   ```bash
   curl -i http://localhost:8080/deals
   curl -i "http://localhost:8080/deals?token=wrong"
   ```
4. **POST /subscribe** - returns a confirmation page containing `/deals?token=...`
   ```bash
   curl -i -d "name=Ann&email=ann@example.com" http://localhost:8080/subscribe
   ```
5. **200 OK with the token** - paste the token from step 4
   ```bash
   curl -i "http://localhost:8080/deals?token=PASTE_TOKEN_HERE"
   ```
6. Extras: `curl -i -d "name=&email=x" http://localhost:8080/subscribe` -> 400;
   `curl -i http://localhost:8080/subscribe` -> 405.

Check `cat subscribers.log` afterwards to see the logged subscriber and token.

**Threading demo** - open a connection that never finishes its request, then show another client is still served:
```bash
python3 -c "import socket,time; s=socket.create_connection(('localhost',8080)); s.send(b'GET / HTTP/1.1\r\n'); time.sleep(8)" &
curl -i http://localhost:8080/
```

## How it maps to networking concepts (for the report)

- **Sockets / TCP**: `server.py` calls `socket(AF_INET, SOCK_STREAM)` (IPv4 + TCP), `bind()` to a port,
  `listen()` (the OS does the TCP 3-way handshake and queues connections) and `accept()`, which returns a
  *new* socket per client. `close()` ends the connection (FIN).
- **TCP is a byte stream**: no message boundaries, so `read_request()` loops on `recv()` until it sees the
  blank line (`\r\n\r\n`) ending the headers, then reads exactly `Content-Length` more bytes for the body.
- **HTTP is application-layer**: a request is plain text (request line, headers, blank line, body) and so is
  the response (status line, headers, blank line, body). `parse_head()` and `build_response()` handle each
  side by hand.
- **Status codes**: 200 OK, 400 Bad Request (bad form), 403 Forbidden (valid request, not allowed - no
  valid token), 404 Not Found, 405 Method Not Allowed, 413 Payload Too Large.
- **Content-Type**: chosen from the file extension so the browser knows how to render the bytes.
- **Threading**: the main loop only calls `accept()`, then starts one thread per connection, so a slow client
  blocked in `recv()` never blocks others. A 10-second socket timeout stops stalled clients holding a thread.
- **Token access control**: `secrets.token_urlsafe` makes an unguessable token at subscribe time; it's
  written to `subscribers.log` and `/deals` only returns 200 if the `?token=` query value is found there.
  Tokens are read from the file, so they stay valid across server restarts.

## Security notes

- Path traversal blocked: requested paths are resolved and must stay inside `public/`.
- User-supplied name/email are HTML-escaped on the confirmation page.
- A lock serialises log writes between threads.
- Tokens in the URL can leak via browser history or `Referer`; fine for this course demo, but a real site
  would use cookies/sessions. Plain HTTP only (no TLS).

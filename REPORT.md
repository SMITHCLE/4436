# Report notes: a socket-based HTTP/1.1 web server

Use this as the skeleton for your write-up and as a script for the demo. Every claim below maps to code you can point at.

## 1. What it is (one paragraph)

A web server written directly on TCP sockets with Python's `socket` module (no `http.server`, no frameworks).
It serves a small business-promotions site: static HTML/CSS/images, a custom 404 page, a subscribe form handled
with POST, and a subscriber-only deals page that returns **403 Forbidden** unless the URL carries a valid
token issued at subscribe time. Each client connection is handled in its own thread.

## 2. Life of one request (the story to tell the TA)

| Step | What happens | Layer | Code |
|---|---|---|---|
| 1 | Browser opens a TCP connection to port 8080 (SYN, SYN-ACK, ACK) | Transport (TCP) | OS does it; `listen()` queues it |
| 2 | Server's `accept()` returns a *new* socket for this client | Sockets API | `server.py` main loop |
| 3 | A new thread is started so the loop can go back to `accept()` | Concurrency | `threading.Thread(...)` |
| 4 | Server calls `recv()` repeatedly until it sees `\r\n\r\n` (end of headers), then reads `Content-Length` more bytes | TCP is a byte stream | `read_request()` |
| 5 | Text is parsed into method, path, query, headers | Application (HTTP) | `parse_head()` |
| 6 | Dispatcher picks a handler by method + path | Application | `routes.handle_request()` |
| 7 | Handler builds `status line + headers + blank line + body` | Application (HTTP) | `routes.build_response()` |
| 8 | `sendall()` writes the bytes; `close()` sends FIN | Transport | `handle_connection()` |

Key point: **HTTP is just agreed-upon text carried inside a TCP byte stream.** TCP gives reliability and order;
HTTP gives meaning (methods, status codes, headers).

## 3. Status codes used

| Code | When | Why this one |
|---|---|---|
| 200 OK | file exists, subscribe succeeded, valid token | success |
| 400 Bad Request | malformed request line, bad `Content-Length`, empty name / bad email | client error in the request itself |
| 403 Forbidden | `/deals` or `/deals.html` without a valid token | server understood the request but refuses; different from 404 because the page exists |
| 404 Not Found | no such file (or path traversal attempt) | resource doesn't exist |
| 405 Method Not Allowed | POST to a page, GET to `/subscribe`, HEAD | path exists, method not supported (`Allow:` header says which is) |
| 413 Payload Too Large | headers > 16 KB or body > 64 KB | protects the server |
| 500 Internal Server Error | unexpected bug in a handler | so a client never hangs |

## 4. Design decisions you can defend

- **Token in the query string** (`/deals?token=...`), as the assignment specified. Tokens come from `secrets.token_urlsafe(16)`
  (128 bits from the OS's secure random source), so they can't be guessed.
- **Tokens persist in `subscribers.log`** (one JSON object per line: timestamp, name, email, token), so they survive
  a restart. JSON lines instead of tab-separated so a name containing a tab or newline can't corrupt the file.
- **Protected pages are matched on the final path** (`/deals` *and* `/deals.html`); otherwise the `.html` URL would bypass the check.
- **Thread per connection** (the assignment's choice). Simple and enough for a demo. A log lock stops two threads
  interleaving writes. A 10-second socket timeout stops a stalled client holding a thread forever.
- **`Connection: close`**: one request per TCP connection. This is simpler than keep-alive; a real server reuses connections.

## 5. Security notes (good to mention; shows you thought about it)

- **Path traversal** (`GET /../server.py`): paths are resolved with `realpath` and must stay inside `public/`. Tested: returns 404.
- **XSS**: the visitor's name/email are HTML-escaped on the confirmation page. Tested with `name=Ann <b>`.
- **Oversized / malformed input**: tested garbage request lines, bad and huge `Content-Length`, null bytes in the path.
  Testing found one bug (a null byte in the URL crashed that connection's thread with no response); it's fixed, and unexpected errors now return a 500.
- **Limitations (say these out loud, they earn credit):** tokens in URLs can leak via browser history or the `Referer` header
  (a real site would use cookies/sessions); no HTTPS/TLS; thread-per-connection doesn't scale to thousands of clients
  (a real server would use a thread pool or async I/O); no keep-alive, caching headers or HEAD support.

## 6. Demo script (3 minutes)

1. `python3 server.py`, open http://localhost:8080 (200, page loads: browser makes separate requests for HTML, CSS, image; point at the server log).
2. Visit `/nope` (404) and `/deals` (403).
3. Submit the subscribe form. Show the confirmation page's token link, then `cat subscribers.log`.
4. Click the link (200). Edit one character of the token and reload (403).
5. Terminal: `curl -i http://localhost:8080/deals` to show the raw response text: this is the HTTP message itself.
6. Threading: run the stalled-client command from the README, then `curl` the site; it still answers instantly.

## 7. Questions a TA might ask

- *Why does `accept()` return a new socket?* The listening socket must stay free to accept more clients; the new socket is the dedicated channel for one connection (identified by client IP+port / server IP+port).
- *Why loop on `recv()`?* TCP has no message boundaries: one `recv()` can return part of a request or several.
- *Why `Content-Length`?* The body has no terminator; the header tells the server how many bytes to read.
- *What's `SO_REUSEADDR`?* Lets you restart immediately instead of waiting for the old socket's TIME_WAIT to expire.
- *403 vs 401?* 401 means "authenticate" (and expects a `WWW-Authenticate` header); 403 means "refused". The spec said 403.
- *What would change for production?* TLS, sessions/cookies instead of URL tokens, a database, a thread pool or async I/O, keep-alive, input validation and rate limiting.

## 8. Where this could go (future work paragraph)

The same server could grow into a multi-business rewards platform: business accounts and logins, businesses creating their own
promotions, customers earning and redeeming points, a database, and real hosting. That needs more than a raw-socket
server, which is why this project stops at the networking foundation.

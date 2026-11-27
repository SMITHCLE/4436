# Report notes: Sunrise Bakery - a socket-based HTTP/1.1 web server

Use this as the skeleton for your write-up and as a script for the presentation.
Every claim maps to code you can point at.

## 1. Background and idea (start here)

**The idea:** a service for small local businesses (e.g. a bakery) to run promotions and reward customers.
A visitor sees this week's promotions, then **subscribes** (name + email) to unlock **exclusive deals**.
Returning customers log in to claim rewards; the more often someone visits, the better their reward
(new, returning, frequent). This is an **MVP / prototype**: the long-term plan is to package it as a
plug-in/extension that a business can drop into its own site.

**What this project builds:** the networking core of that idea, from scratch, with raw TCP sockets and Python's standard library:
the web server, the subscribe + login flow, per-visit tokens, and a token-protected deals page.

## 2. Idea -> course concepts (the mapping your prof asked for)

| Feature of the site | Networking concept | Where in the code |
|---|---|---|
| Browser connects to the site | TCP connection, 3-way handshake, port 8080; `socket/bind/listen/accept` | `server.py` `main()` |
| Loading a page | HTTP **GET**: request line + headers, response status line + headers + body | `parse_head()`, `build_response()` |
| Subscribe form / login form | HTTP **POST**: data travels in the *body* (`application/x-www-form-urlencoded`), sized by `Content-Length` | `read_request()`, `handle_subscribe()`, `handle_login()` |
| Clicking "view your deals" | A new GET request to another URL, carrying the token in the **query string** | `serve_deals()` |
| Images on the deals page | Each image is a **separate HTTP request**; body is binary and `Content-Type` is `image/png` or `image/jpeg` | `serve_static()`, `CONTENT_TYPES` |
| Not subscribed -> friendly message | **403 Forbidden** (page exists, access refused) vs **404 Not Found** | `serve_deals()`, `error_page()` |
| Many customers at once | **One thread per connection**; a slow client can't block others | `threading.Thread` in `main()` |
| "Every visit has its own token" | Per-session tokens (`secrets.token_urlsafe`) logged with a timestamp; HTTP is **stateless**, so the token is how the server recognises a visit | `log_visit()`, `find_by_token()` |
| New / returning / frequent deals | Server-side state (the log) used to personalise the response | `tier_for()`, `tier_block()` |

## 3. Life of one request

| Step | What happens | Layer | Code |
|---|---|---|---|
| 1 | Browser opens a TCP connection to port 8080 (SYN, SYN-ACK, ACK) | Transport (TCP) | OS does it; `listen()` queues it |
| 2 | `accept()` returns a *new* socket for this client | Sockets API | `server.py` main loop |
| 3 | A new thread starts so the loop can go back to `accept()` | Concurrency | `threading.Thread(...)` |
| 4 | `recv()` repeatedly until `\r\n\r\n` (end of headers), then `Content-Length` more bytes | TCP is a byte stream | `read_request()` |
| 5 | Text parsed into method, path, query, headers | Application (HTTP) | `parse_head()` |
| 6 | Dispatcher picks a handler by method + path | Application | `routes.handle_request()` |
| 7 | Handler builds `status line + headers + blank line + body` | Application (HTTP) | `routes.build_response()` |
| 8 | `sendall()` writes the bytes; `close()` sends FIN | Transport | `handle_connection()` |

Key point: **HTTP is just agreed-upon text carried inside a TCP byte stream.**

## 4. Customer tiers (the "rewards" logic)

Every subscribe and every login appends one line to `subscribers.log` (timestamp, event, name, email, **new token**).
The number of lines for an email = that customer's visit count.

| Visits | Tier | Deals page shows |
|---|---|---|
| 1 | New | Welcome message + how to unlock rewards |
| 2-3 | Returning | Bonus: free coffee with any purchase |
| 4+ | Frequent | Bonus: 15% off the whole order + free cupcake with any cake |

Each visit gets a fresh token, so each session is recorded separately and the page the customer sees is generated
per request (not a static file). Thresholds are two constants in `routes.py` (`RETURNING_AT`, `FREQUENT_AT`).

## 5. Status codes used

| Code | When | Why this one |
|---|---|---|
| 200 OK | file exists, subscribe/login succeeded, valid token | success |
| 400 Bad Request | malformed request line, bad `Content-Length`, empty name / bad email | client error |
| 403 Forbidden | `/deals` without a valid token; login with an email that never subscribed | understood, but refused (page exists, unlike 404) |
| 404 Not Found | no such file (or path traversal attempt) | doesn't exist |
| 405 Method Not Allowed | wrong method for a path (e.g. GET `/subscribe`, HEAD) | `Allow:` header says which is OK |
| 413 Payload Too Large | headers > 16 KB or body > 64 KB | protects the server |
| 500 Internal Server Error | unexpected bug in a handler | client never hangs |

## 6. Design decisions you can defend

- **Token in the query string** (`/deals?token=...`), as specified. 128 random bits from the OS, so unguessable.
- **A log file, not a database.** `subscribers.log` is JSON-per-line so a name with a tab or newline can't corrupt it.
  Tokens persist across restarts because they're read back from the file.
- **Login by email only (no password)**: fine for a prototype; a real site needs real authentication.
- **Protected pages are matched on the final path** (`/deals` *and* `/deals.html`) so the `.html` URL can't bypass the check.
- **Thread per connection** with a 10-second client timeout and a lock around log writes.
- **`Connection: close`**: one request per TCP connection (simpler than keep-alive).

## 7. Security notes

- Path traversal (`GET /../server.py`): paths resolved with `realpath` must stay inside `public/` -> 404.
- XSS: names/emails are HTML-escaped (tested with `<script>`).
- Malformed input tested: garbage request line, bad/huge `Content-Length`, null byte in path (found and fixed a crash).
- **Limitations (say them out loud):** tokens in URLs leak via history/`Referer`; email-only login means anyone who knows
  an email can log in as that customer; no HTTPS; thread-per-connection doesn't scale to thousands of clients;
  no keep-alive, caching or HEAD.

## 8. Presentation script (about 4 minutes)

1. **Idea (30 s).** "Small local businesses want to reward customers. My MVP: visitors see promotions, subscribe to unlock exclusive deals, and returning customers log in for rewards. I built the networking core from scratch with raw sockets."
2. **Demo (90 s)** with the terminal visible beside the browser:
   - Home page loads: point at the terminal lines for `/`, `/style.css`, `/images/logo.png` ("one page = several separate HTTP requests").
   - Click the subscribe prompt area, then `/deals` without a token: **403**. Type `/nope`: **404**.
   - Subscribe (POST). Show the token link, then the deals page with the three images ("images are fetched as their own GET requests, `image/png` and `image/jpeg`").
   - Home page -> **Click to get your rewards** -> log in with the same email a few times. Show the banner change New -> Returning -> Frequent.
   - Open `subscribers.log`: "one line per visit, each with a timestamp and a new token."
   - Log in with an unsubscribed email: friendly 403.
3. **Code (75 s).** `server.py` socket lifecycle (~lines 155-164) -> thread per connection -> `read_request()` -> `routes.py` `build_response()` -> `tier_for()`.
4. **Limits + future (30 s).** Email-only login, tokens in URLs, no HTTPS/database. Next step: package it as a plug-in/extension a business adds to its own site, with a database and business-defined promotions.

## 9. Questions a TA might ask

- *Why does `accept()` return a new socket?* The listening socket must stay free for more clients.
- *Why loop on `recv()`?* TCP has no message boundaries.
- *Why `Content-Length`?* The body has no terminator; the header says how many bytes to read.
- *GET vs POST here?* GET fetches pages/images and carries the token in the URL; POST sends form data in the body.
- *Why a new token each login?* HTTP is stateless; the token identifies a visit, and logging it gives the visit count.
- *403 vs 401?* 401 = authenticate; 403 = refused. The spec said 403.
- *What would change for production?* TLS, sessions/cookies, passwords, a database, a thread pool or async I/O, keep-alive.

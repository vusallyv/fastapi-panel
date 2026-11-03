# Security

An admin panel can reach all your data, so its defaults are strict and don't depend on you turning anything on.

## What the panel handles

| Threat | Protection |
|---|---|
| Cross-site request forgery | A per-session token on every POST, including login, plus `Origin` and `Sec-Fetch-Site` checks |
| Session theft through scripts | `HttpOnly` cookie, and a Content-Security-Policy with no inline scripts or styles |
| Session tampering | HMAC-SHA256 signed cookie, compared in constant time |
| Sessions outliving access | Each request reloads the user. Deactivating them or changing their password ends the session |
| Session fixation | The session and CSRF token are replaced on login and logout |
| Password guessing | 5 failed attempts per username and IP, then a 15 minute lockout |
| Username discovery | One error message, and unknown usernames still cost a password check |
| Password theft from the database | scrypt with a per-password salt |
| Cross-site scripting | Autoescaped templates and the CSP. The audit log and flash messages are escaped too |
| Clickjacking | `frame-ancestors 'none'` and `X-Frame-Options: DENY` |
| SQL injection | All queries go through SQLAlchemy with bound parameters. `LIKE` wildcards are escaped |
| Mass assignment | Only fields on the form are written. Read-only fields and primary keys are ignored |
| Probing data through URLs | Sorting and filtering are allowlisted, unknown parameters are ignored |
| Rows from another parent or tenant | Inline rows must belong to the parent, and all access goes through `get_queryset` |
| Open redirects | `next` must stay inside the panel |
| Secrets on screen | Columns named like credentials are hidden unless listed explicitly |
| Secrets in logs | The audit log records field names, not values |
| Spreadsheet formula injection | CSV cells starting with `=`, `+`, `-` or `@` are prefixed with `'` |
| Oversized requests | POST bodies have a size limit, inline forms a row limit, and bulk actions a row limit |
| Misconfiguration | It won't start without an auth backend, or with a secret under 32 characters. Plain text passwords are refused in `StaticAuth`, and `ModelAuth` won't guess whether someone is staff |

## What's left to you

**Use HTTPS.** Cookies get the `Secure` flag on HTTPS requests. Behind a proxy, make sure the app sees the real scheme, with `uvicorn --proxy-headers --forwarded-allow-ips=<proxy ip>`. Or force it with `secure_cookies=True`, which is a good idea in production anyway.

**Check the Host header.** `Origin` is compared to the request's host. If your proxy rewrites `Host`, POSTs get refused until you pass the public address: `trusted_origins=["https://admin.example.com"]`. Use FastAPI's `TrustedHostMiddleware` so that clients can't choose the host themselves.

**Forward the real client IP.** Login throttling goes by IP and username. Without `--proxy-headers` every client looks like your proxy, which means one attacker can lock out a username for everyone.

**Keep `secret_key` secret.** With it, anyone can forge a session. Load it from the environment, use a different one per environment, and rotate it if it might have leaked. Rotating logs everybody out.

**Consider limiting who can reach it.** Serve the panel on a VPN or restrict it by IP if you can. There's no built-in two-factor login yet, so a reverse proxy with SSO in front is the best way to add one today.

**Mind the database user.** Actions run with your app's database credentials. Foreign key and unique rules still apply, and the panel shows a generic message if they fail.

## Known limits

- Throttling counters live in the process, so with `N` workers an attacker gets up to `5 × N` attempts per window. Put a shared rate limit in your proxy if that matters.
- Sessions are stored in the cookie, so a single session can't be revoked from the server. See [Sessions](auth-and-permissions.md#sessions).
- `max_body_size` reads the `Content-Length` header. Requests without one, such as chunked uploads, aren't limited here, so also set a limit in your proxy.
- Audit entries are only written for changes made through the panel, not for direct database changes.

## Reporting a vulnerability

See [SECURITY.md](../SECURITY.md).

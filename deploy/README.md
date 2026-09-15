# Hosting InferiaLLM behind a reverse proxy

As of the single-port consolidation, the `inferia-app` container serves the
**entire** web surface from **one** port (`APP_PORT`, default `8000`):

| Path        | Served by                  | Notes |
|-------------|----------------------------|-------|
| `/`         | dashboard SPA              | client-side routes fall back to `index.html` |
| `/api/...`  | API gateway                | `/api/auth`, `/api/v1/*` compute, `/api/v1/workers/*` (control **WebSocket** + shell/logs) |
| `/inf/...`  | inference API              | OpenAI-compatible `/inf/v1/*` (chat completions stream as **SSE**) |

Because it is all one origin, **there is no CORS to configure** and the proxy
does **no** path rewriting. The app does all internal routing.

## The only rule

> **Forward every path, verbatim, to `inferia-app:${APP_PORT}` (default 8000).**
> Enable **WebSocket upgrades**, **disable response buffering** (SSE streaming),
> allow **large bodies**, and use **long timeouts**.

That's the whole job. Everything below is that rule expressed for each tool.

### Why those four settings

| Setting | Needed by |
|---|---|
| WebSocket upgrade (`Upgrade`/`Connection`) | worker control channel `/api/v1/workers/channel`, node shell/logs tunnels |
| `proxy_buffering off` (+ `X-Accel-Buffering: no`) | SSE token streaming on `/inf/v1/chat/completions`; without it tokens arrive in one lump at the end |
| `proxy_max_temp_file_size 0` + long timeouts | long-lived streamed responses (deploy waits, log tails) |
| large `client_max_body_size` (≈200m) | image/video generation + embedding batch requests |

---

## Required environment

The dashboard and workers must agree with the proxy on the host. Defaults are
already same-origin (`/api`, `/inf`), so for a plain single-domain deploy you
can leave the `DASHBOARD_*` URLs unset. Set them explicitly only if you serve
the dashboard on a different origin than the API:

```dotenv
APP_PORT=8000
# Same-origin defaults: DASHBOARD_API_GATEWAY_URL=/api, DASHBOARD_INFERENCE_URL=/inf
# Set the full URL only for a cross-origin / absolute setup:
DASHBOARD_API_GATEWAY_URL=https://inferia.example.com/api
DASHBOARD_INFERENCE_URL=https://inferia.example.com/inf

# Worker-facing (must be reachable from the GPU hosts):
INFERIA_CONTROL_PLANE_EXTERNAL_URL=https://inferia.example.com/api
```

---

## Option 1 — No proxy (dev / trusted network)

Just publish the port (compose already maps `${APP_PORT:-8000}`) and hit it directly:

```bash
docker compose up -d
# → http://localhost:8000/  (dashboard, /api, /inf all here)
```

No TLS, no host routing. Fine for local dev or a private network.

---

## Option 2 — Caddy (simplest TLS)

Caddy's `reverse_proxy` streams responses and handles WebSocket upgrades
**automatically** — no buffering/upgrade directives needed.

For a **public domain with automatic Let's Encrypt**, a whole Caddyfile is two lines:

```caddyfile
inferia.example.com {
	reverse_proxy inferia-app:{$APP_PORT:8000}
}
```

Run Caddy as a compose service on the `inferia-net` network with ports `80:80`
and `443:443`; mount the Caddyfile and a `caddy_data` volume (for certs). Drop
the app's public `ports:` mapping once Caddy fronts it.

---

## Option 3 — nginx (hand-rolled)

Use the provided **`deploy/nginx.conf`** (already written for the single
upstream). Add this service to your compose file:

```yaml
  proxy:
    image: nginx:1.27-alpine
    container_name: inferia-proxy
    restart: unless-stopped
    depends_on: [app]
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./deploy/nginx.conf:/etc/nginx/nginx.conf:ro
      - ./deploy/certs:/etc/nginx/certs:ro       # fullchain.pem + privkey.pem
      - ./deploy/certbot-www:/var/www/certbot    # only for certbot http-01
    networks:
      - inferia-net
```

Edit `server_name` + the cert paths in `nginx.conf`, then drop the app's public
`ports:`. If `APP_PORT` is not `8000`, change the `upstream inferia_app` line.

If you maintain your own nginx and only want the **server block**, the entire
location is:

```nginx
upstream inferia_app { server inferia-app:8000; keepalive 32; }

map $http_upgrade $connection_upgrade { default upgrade; '' close; }

server {
    listen 443 ssl;
    http2 on;
    server_name inferia.example.com;
    ssl_certificate     /etc/nginx/certs/fullchain.pem;
    ssl_certificate_key /etc/nginx/certs/privkey.pem;

    client_max_body_size 200m;

    location / {
        proxy_pass http://inferia_app;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade           $http_upgrade;
        proxy_set_header Connection        $connection_upgrade;
        proxy_buffering          off;
        proxy_request_buffering  off;
        proxy_max_temp_file_size 0;
        add_header X-Accel-Buffering no always;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
```

---

## Option 4 — Nginx Proxy Manager (NPM, the web GUI)

Create **one Proxy Host** (NPM proxies the whole hostname to one upstream —
exactly what the single-port app wants; no per-path setup).

**Networking first:** put the NPM container on the same Docker network as
`inferia-app` (so the name `inferia-app` resolves), **or** forward to the host's
published port (`<docker-host-IP>:8000`).

**Proxy Hosts → Add Proxy Host:**

- **Details** tab
  - **Domain Names:** `inferia.example.com`
  - **Scheme:** `http`
  - **Forward Hostname / IP:** `inferia-app` *(same network)* — or your host IP
  - **Forward Port:** `8000`  *(= APP_PORT)*
  - **Cache Assets:** OFF
  - **Block Common Exploits:** ON *(optional)*
  - **Websockets Support:** **ON**  ← required (worker channel, shell/logs)
- **SSL** tab
  - **SSL Certificate:** *Request a new Let's Encrypt certificate* (or upload one)
  - **Force SSL:** ON · **HTTP/2 Support:** ON · **HSTS:** optional
- **Advanced** tab — paste this **Custom Nginx Configuration** (NPM's UI does
  not expose buffering/timeouts, and these are required for SSE streaming +
  large model pulls):

  ```nginx
  proxy_buffering off;
  proxy_request_buffering off;
  proxy_max_temp_file_size 0;
  client_max_body_size 200m;
  proxy_read_timeout 3600s;
  proxy_send_timeout 3600s;
  add_header X-Accel-Buffering no always;
  ```

NPM already injects `Host` / `X-Forwarded-*` and (with **Websockets Support ON**)
the `Upgrade`/`Connection` headers, so you do **not** add those in Advanced.

> Do **not** create separate Proxy Hosts / custom locations for `/gw` or
> `/inf` — that was the old multi-port layout. One Proxy Host for the whole
> domain → port 8000 is correct now.

---

## Option 5 — Traefik (labels)

On the `app` service in compose:

```yaml
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.inferia.rule=Host(`inferia.example.com`)"
      - "traefik.http.routers.inferia.entrypoints=websecure"
      - "traefik.http.routers.inferia.tls.certresolver=le"
      - "traefik.http.services.inferia.loadbalancer.server.port=8000"
```

Traefik streams and handles WebSockets by default. For very long model pulls,
raise the entrypoint/transport timeouts (`--entrypoints.websecure.transport.respondingTimeouts.readTimeout=3600s`).

---

## Option 6 — Cloudflare Tunnel (`cloudflared`, no open ports)

`config.yml`:

```yaml
tunnel: <tunnel-id>
credentials-file: /etc/cloudflared/<tunnel-id>.json
ingress:
  - hostname: inferia.example.com
    service: http://inferia-app:8000
  - service: http_status:404
```

WebSockets work over Tunnel by default. Note Cloudflare's **100 MB request body
limit** (free/pro) — large image/video generation uploads may need an
Enterprise plan or a direct origin; download *responses* are unaffected.

---

## Verify any setup

```bash
H=https://inferia.example.com
curl -sf  $H/                       # 200, dashboard HTML
curl -sf  $H/config.js              # contains window.__RUNTIME_CONFIG__ (/api, /inf)
curl -sf  $H/api/health             # 200
curl -s   $H/inf/v1/models          # routes to inference (200, or 401 if it requires a token)
# WebSocket (needs a token): wss://inferia.example.com/api/v1/workers/channel
```

If the dashboard loads but live logs/shell or token streaming hang, your proxy
is **buffering** or missing the **WebSocket upgrade** — recheck the two settings
above.

# Guide to Setting Up Cloudflare Tunnel (Forwarding localhost:8000)

This guide walks you through creating a Cloudflare Tunnel to securely expose a local application running on `localhost:8000` to the internet.

## Prerequisites

* A Cloudflare account
* A domain on Cloudflare (required for permanent named tunnels)
* A server or VM with internet access where you will install `cloudflared`

> **Addendum – No domain? Use Quick Tunnels instead**
> If you don't have your own domain on Cloudflare, you can still expose `localhost:8000` using Quick Tunnels and a `trycloudflare.com` subdomain. Skip to the [Quick tunnels (development)](#quick-tunnels-development) section below.

---

## Step 1 – Install `cloudflared`

Instead of duplicating the install steps, follow the official Cloudflare documentation to install the `cloudflared` daemon on your system:

➡️ **[Official `cloudflared` Installation Guide](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/downloads/)**

Once `cloudflared` is installed and available in your system's PATH, proceed to create your tunnel.

---

## Step 2 – Create a Tunnel

You can create a tunnel using the Cloudflare Dashboard or the `cloudflared` CLI.

### Option A: Using the Dashboard

1. Log in to the [Cloudflare dashboard](https://dash.cloudflare.com/).
2. Go to **Networking** > **Tunnels**.
3. Select **Create Tunnel**.
4. Enter a name for your tunnel (for example, `inferia`) and select **Create Tunnel**.
5. Under **Setup Environment**, select the operating system and architecture of your server.
6. Copy the install command shown under **Install and Run** and run it in a terminal on your server. This command includes your tunnel token and installs `cloudflared` as a system service.
7. Once the tunnel connects, select **Continue**.

### Option B: Using the CLI

If you prefer to manage your tunnel via the command line instead of the dashboard:

1. Authenticate `cloudflared` with your Cloudflare account:
   ```bash
   cloudflared tunnel login
   ```
2. Create a new tunnel:
   ```bash
   cloudflared tunnel create inferia
   ```

---

## Step 3 – Publish your application (localhost:8000)

### Option A: Using the Dashboard

1. In the Cloudflare dashboard, go to **Networking** > **Tunnels** and select your tunnel.
2. Under **Routes**, select **Add route**.
3. Select **Published application**.
4. Under **Hostname**, enter a subdomain and select your domain (e.g., `inferia.yourdomain.com`).
5. For **Service URL**, enter `http://localhost:8000`.
6. Select **Add route**.

### Option B: Using the CLI

1. Create a configuration file for your tunnel at `~/.cloudflared/config.yml`:

   ```yaml
   tunnel: <YOUR_TUNNEL_ID>
   credentials-file: /home/user/.cloudflared/<YOUR_TUNNEL_ID>.json

   ingress:
     - hostname: inferia.yourdomain.com
       service: http://localhost:8000
     - service: http_status:404
   ```

2. Create a DNS CNAME record:
   ```bash
   cloudflared tunnel route dns inferia inferia.yourdomain.com
   ```

3. Run the tunnel:
   ```bash
   cloudflared tunnel run inferia
   ```

---

## Quick tunnels (development)

For local development, instantly expose localhost without a Cloudflare account:

```bash
cloudflared tunnel --url http://localhost:8000
```

This generates a random `trycloudflare.com` subdomain (e.g., `random-words.trycloudflare.com`). Anyone with that URL can reach your local service on port 8000. The URL changes every restart — use this only for testing.

---

## Configuring InferiaLLM for the tunnel

After the tunnel is live, update your `.env` (copy from `.env.example`) so InferiaLLM knows its public URL:

| Variable | Value | Purpose |
|----------|-------|---------|
| `INFERIA_CONTROL_PLANE_EXTERNAL_URL` | `https://inferia.yourdomain.com/gw` | Workers dial back to this URL |
| `DASHBOARD_API_GATEWAY_URL` | `https://inferia.yourdomain.com/gw` | Dashboard calls API gateway via this |
| `DASHBOARD_INFERENCE_URL` | `https://inferia.yourdomain.com/inf` | Dashboard calls inference via this |
| `DASHBOARD_WEB_SOCKET_URL` | `wss://inferia.yourdomain.com/gw` | WebSocket connections |
| `ALLOWED_ORIGINS` | Add `https://inferia.yourdomain.com` | CORS allowlist |
| `FORWARDED_ALLOW_IPS` | `"172.16.0.0/12"` | Trust X-Forwarded-For from Docker |

Replace `inferia.yourdomain.com` with your actual tunnel hostname. For Quick Tunnels, use the `trycloudflare.com` URL (e.g. `https://random-words.trycloudflare.com`).

1. Edit your `.env` file with the values above.
2. Re-initialize the database to pick up the new URL (this re-writes the internal org URL):
   ```bash
   inferiallm init --env production
   ```
3. Start InferiaLLM:
   ```bash
   inferiallm start
   ```

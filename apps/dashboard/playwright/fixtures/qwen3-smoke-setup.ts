import { execSync } from "node:child_process";
import path from "node:path";

const REPO_ROOT = path.resolve(__dirname, "../../../..");
const COMPOSE = path.join(REPO_ROOT, "deploy", "compose.worker-local.yml");
const GATEWAY = process.env.PLAYWRIGHT_GATEWAY_URL ?? "http://localhost:8000";
const ADMIN_EMAIL = process.env.PLAYWRIGHT_ADMIN_EMAIL ?? "admin@inferia.local";
const ADMIN_PASSWORD = process.env.PLAYWRIGHT_ADMIN_PASSWORD ?? "admin";

interface SetupResult {
  poolId: string;
  poolName: string;
  workerNodeName: string;
}

async function api<T>(token: string | null, method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (token) headers.authorization = `Bearer ${token}`;
  const res = await fetch(`${GATEWAY}${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`${method} ${path} → ${res.status} ${await res.text()}`);
  return (await res.json()) as T;
}

export async function setupLocalWorker(): Promise<SetupResult> {
  const auth = await api<{ access_token: string }>(null, "POST", "/v1/auth/login", {
    email: ADMIN_EMAIL, password: ADMIN_PASSWORD,
  });
  const token = auth.access_token;
  const poolName = `pw-smoke-${Date.now().toString(36)}`;
  const pool = await api<{ id: string }>(token, "POST", "/v1/compute-pools", {
    provider: "worker", name: poolName,
  });
  const bs = await api<{ token: string }>(token, "POST", "/v1/admin/workers/mint", {
    pool_id: pool.id, ttl_hours: 1,
  });
  const env = {
    ...process.env,
    BOOTSTRAP_TOKEN: bs.token,
    POOL_ID: pool.id,
    INFERENCE_TOKEN: require("node:crypto").randomBytes(32).toString("hex"),
    NODE_NAME: "pw-smoke-1",
  };
  execSync(`docker compose -f ${COMPOSE} up -d`, { env, stdio: "inherit" });
  const deadline = Date.now() + 60_000;
  for (;;) {
    const w = await api<{ workers: Array<{ status: string }> }>(
      token, "GET", `/v1/admin/workers?pool=${pool.id}`,
    );
    if (w.workers.some(x => x.status === "ready")) break;
    if (Date.now() > deadline) throw new Error("worker did not become ready in 60s");
    await new Promise(r => setTimeout(r, 2_000));
  }
  return { poolId: pool.id, poolName, workerNodeName: "pw-smoke-1" };
}

export async function teardownLocalWorker(state: SetupResult): Promise<void> {
  try {
    execSync(`docker compose -f ${COMPOSE} down -v`, { stdio: "inherit" });
  } catch (e) {
    console.error("compose down failed", e);
  }
  try {
    const auth = await api<{ access_token: string }>(null, "POST", "/v1/auth/login", {
      email: ADMIN_EMAIL, password: ADMIN_PASSWORD,
    });
    await api(auth.access_token, "POST", `/v1/compute-pools/${state.poolId}:destroy`);
  } catch (e) {
    console.error("pool destroy failed", e);
  }
}

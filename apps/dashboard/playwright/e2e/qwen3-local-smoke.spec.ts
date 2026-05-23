import { expect, test } from "@playwright/test";
import { setupLocalWorker, teardownLocalWorker } from "../fixtures/qwen3-smoke-setup";

type Setup = Awaited<ReturnType<typeof setupLocalWorker>>;
let state: Setup;

test.beforeAll(async () => { state = await setupLocalWorker(); });
test.afterAll(async () => { if (state) await teardownLocalWorker(state); });

test.describe.configure({ mode: "serial" });

test("ollama Qwen3 deployment is chattable from sandbox", async ({ page }) => {
  test.setTimeout(5 * 60_000);

  await page.goto("/login");
  await page.getByLabel(/email/i).fill(process.env.PLAYWRIGHT_ADMIN_EMAIL ?? "admin@inferia.local");
  await page.getByLabel(/password/i).fill(process.env.PLAYWRIGHT_ADMIN_PASSWORD ?? "admin");
  await page.getByRole("button", { name: /sign in|login/i }).click();
  await expect(page).toHaveURL(/\/overview|\/dashboard/);

  await page.getByRole("link", { name: /compute/i }).click();
  await page.getByText(state.poolName).click();
  await expect(page.getByText(state.workerNodeName)).toBeVisible({ timeout: 30_000 });

  await page.getByRole("link", { name: /deployments/i }).click();
  await page.getByRole("button", { name: /new deployment/i }).click();
  await page.getByText(/inference/i).first().click();
  await page.getByText(/ollama/i, { exact: false }).first().click();
  await page.getByPlaceholder(/model name|search/i).fill("qwen3:0.6b");
  await page.getByText(/qwen3.*0\.6/i).first().click();
  await page.getByText(state.poolName).click();
  await page.getByRole("button", { name: /deploy/i }).click();

  await expect(page.getByText("Running")).toBeVisible({ timeout: 3 * 60_000 });

  await page.getByRole("link", { name: /sandbox/i }).click();
  const input = page.getByPlaceholder(/type a message|ask/i);
  await input.fill("say hello in one short sentence");
  await page.getByRole("button", { name: /send/i }).click();

  const assistantArea = page.locator('[data-role="assistant"], .assistant-message').first();
  await expect(assistantArea).toContainText(/\w+/, { timeout: 90_000 });
});

/**
 * End-to-end UI test for the AWS provisioning flow.
 *
 * Two scenarios:
 *   1. Happy path: configure AWS credentials -> open Add Pool wizard
 *      -> pick instance class + type -> Create -> wait for phase=ready
 *      -> assert the Instance ID is rendered in the AWS metadata grid.
 *   2. Failure path: configure deliberately-bad credentials -> open the
 *      wizard -> submit -> assert the InvalidCredentials error banner
 *      + the Settings -> Providers -> AWS hint + the Retry button.
 *
 * Setup status (read before running):
 *   This project does NOT currently declare ``@playwright/test`` in
 *   apps/dashboard/package.json -- the existing spec under
 *   playwright/e2e/qwen3-local-smoke.spec.ts is also a stub awaiting
 *   the install. To execute this spec:
 *
 *     cd apps/dashboard
 *     npm install --save-dev @playwright/test
 *     npx playwright install chromium
 *     # add a playwright.config.ts with testDir: 'playwright' and
 *     # baseURL pointing at the running dashboard dev server, then:
 *     npx playwright test playwright/aws-provision.spec.ts
 *
 * The spec is therefore committed as runnable code that documents the
 * e2e intent; treat it as an executable test rather than prose.
 */
import { expect, test } from "@playwright/test";


test("happy path: configure -> wizard -> provision -> ready", async ({ page }) => {
  // Provisioning a real EC2 instance and waiting for it to bootstrap
  // can take several minutes even with mocked AWS APIs. Bump the
  // per-test timeout so we don't false-fail before the worker comes up.
  test.setTimeout(5 * 60_000);

  // 1. Land on the dashboard (login flow is handled by the project's
  // standard Playwright auth fixture -- this spec assumes the test
  // harness has already signed an admin user in via storageState).
  await page.goto("/dashboard");

  // 2. Configure AWS creds via Settings -> Providers -> AWS. In a real
  // CI run these creds point at a mocked AWS API (moto / localstack)
  // so we exercise the wizard without spending real money.
  await page.goto("/dashboard/settings/providers/aws");
  await page.fill("input[name=access_key_id]", "AKIA-TEST");
  await page.fill("input[name=secret_access_key]", "test-secret");
  await page.click("button:has-text('Save')");
  await expect(page.locator("text=Saved")).toBeVisible();

  // 3. Open the Add Pool wizard for AWS.
  await page.click("text=Compute");
  await page.click("text=Add Pool");
  await page.click("text=AWS");

  // 4. Normal GPU tab is the default; pick g6.xlarge from the catalog
  // grid (catalog is served from GET /v1/nodes/instance-catalog which
  // T22 added).
  await expect(page.locator("text=Normal GPU")).toBeVisible();
  await page.click("text=g6.xlarge");
  await page.fill("input[name=region]", "us-east-1");
  await page.click("text=Create");

  // 5. Land on InstanceDetail. Wait for the Overview tab to render
  // phase=ready (the state-machine path emits this when the
  // BootstrapHandler observes compute_inventory.state='ready').
  await page.waitForURL("**/compute/nodes/**");
  await expect(page.locator("text=ready")).toBeVisible({ timeout: 60_000 });

  // 6. Assert the AWS metadata grid surfaces the Pulumi stack outputs.
  // Instance ID is the canonical "this thing actually exists in AWS"
  // signal; the grid is rendered by AWSMetadataGrid from T29.
  await expect(page.locator("text=Instance ID")).toBeVisible();
});


test("failure path: bad creds shows banner + Retry", async ({ page }) => {
  // 1. Configure deliberately-wrong AWS creds. verify_credentials in
  // PreflightHandler will reject these on the first reconciler tick
  // and the classifier maps the resulting boto3 ClientError to
  // INVALID_CREDENTIALS (PERMANENT) -- no retry, banner stays up.
  await page.goto("/dashboard/settings/providers/aws");
  await page.fill("input[name=access_key_id]", "AKIA-INVALID");
  await page.fill("input[name=secret_access_key]", "wrong");
  await page.click("button:has-text('Save')");
  await expect(page.locator("text=Saved")).toBeVisible();

  // 2. Open the wizard and submit.
  await page.goto("/dashboard/compute/nodes/new");
  await page.click("text=AWS");
  await expect(page.locator("text=Normal GPU")).toBeVisible();
  await page.click("text=g6.xlarge");
  await page.fill("input[name=region]", "us-east-1");
  await page.click("text=Create");

  // 3. Should land on InstanceDetail. Wait for phase=failed.
  await page.waitForURL("**/compute/nodes/**");
  await expect(page.locator("text=failed")).toBeVisible({ timeout: 30_000 });

  // 4. Assert the InvalidCredentials banner + hint + Retry button. The
  // banner text comes from the classifier's message field, the hint
  // from its hint field, and the Retry button is rendered by
  // RetryButton (T29) only when terminal=true AND error.class is
  // not 'TRANSIENT' -- which PERMANENT InvalidCredentials satisfies.
  await expect(page.locator("text=AWS credentials rejected")).toBeVisible();
  await expect(page.locator("text=Settings -> Providers -> AWS")).toBeVisible();
  await expect(page.locator("button", { hasText: "Retry" })).toBeVisible();
});

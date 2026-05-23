import { test, expect } from '@playwright/test';

// SSO end-to-end smoke. Asserts the full Authorization Code + PKCE flow:
//
//   1. Browser loads dashboard at https://inferia.local/
//   2. AUTH_PROVIDER=external → page shows "Sign in with Inferia" button
//   3. Click → /auth/start sets PKCE/state cookies, 302 to inferia-auth's
//      /oauth/authorize which (no SSO cookie present) redirects to /ui/login.
//   4. Submit credentials → POST /api/v1/auth/login → SSO session cookie set,
//      browser redirected back to /oauth/authorize → /auth/callback → dashboard
//      with #access_token=… fragment.
//   5. Dashboard's App.tsx consumes the fragment into tokenStore and replaces
//      the URL. We then verify /auth/me returns the expected identity.
//
// Test user is seeded by scripts/sso_smoke.sh before this spec runs.

const TEST_EMAIL = process.env.SMOKE_EMAIL || 'smoke@inferia.local';
const TEST_PASSWORD = process.env.SMOKE_PASSWORD || 'smoke-password-1234';

test('SSO login flow → access token issued and /auth/me succeeds', async ({ page, context }) => {
  // Step 1 — dashboard root.
  await page.goto('/');

  // Step 2 — external mode renders the IdP button.
  const signInButton = page.getByRole('button', { name: /Sign in with Inferia/i });
  await expect(signInButton).toBeVisible({ timeout: 30_000 });

  // Capture the redirect chain so we can pinpoint where a regression breaks
  // it (gateway /auth/start, IdP /oauth/authorize, /ui/login, callback, …).
  const redirectURLs: string[] = [];
  page.on('framenavigated', (frame) => {
    if (frame === page.mainFrame()) redirectURLs.push(frame.url());
  });

  await signInButton.click();

  // Step 3 — we should land on inferia-auth's login UI (the IdP redirects
  // unauthenticated users from /oauth/authorize to /ui/login?return_to=…).
  await page.waitForURL(/auth\.inferia\.local\/(ui\/)?login/, { timeout: 20_000 });

  // Step 4 — submit credentials. The UI's Login route uses these inputs.
  await page.getByPlaceholder(/you@company\.com|email/i).fill(TEST_EMAIL);
  await page.getByPlaceholder(/password/i).fill(TEST_PASSWORD);
  await page.getByRole('button', { name: /Sign in|Log in/i }).click();

  // Step 5 — wait until we are back on the dashboard origin. The fragment
  // may still be present (#access_token=…) right after the IdP redirect;
  // App.tsx then clears it.
  await page.waitForURL(/inferia\.local\//, { timeout: 30_000 });

  // The dashboard might render multiple things post-login; the most reliable
  // assertion is that a session cookie chain exists and /auth/me succeeds.
  // We harvest cookies from the browsing context and replay them via the
  // APIRequestContext to validate against the gateway.
  const cookies = await context.cookies();
  const cookieHeader = cookies
    .map((c) => `${c.name}=${c.value}`)
    .join('; ');

  // First try to read the access token the SPA may have stashed on the
  // window for test purposes. If absent, fall back to cookies-only — the
  // gateway's middleware accepts the refresh-token cookie path too.
  const accessToken = await page.evaluate(() => {
    return (
      (window as unknown as { __inferia_test_access_token?: string }).__inferia_test_access_token ||
      // Read from localStorage if the dashboard's tokenStore writes there.
      window.localStorage.getItem('inferia.access_token') ||
      null
    );
  });

  const headers: Record<string, string> = {};
  if (accessToken) headers['Authorization'] = `Bearer ${accessToken}`;
  if (cookieHeader) headers['Cookie'] = cookieHeader;

  const resp = await page.request.get('https://inferia.local/auth/me', { headers });

  if (resp.status() !== 200) {
    // Surface diagnostic context: where the chain landed, which cookies we
    // had, and the gateway's response body. Makes E2 failures debuggable.
    const body = await resp.text();
    console.error('--- /auth/me failed ---');
    console.error('status:', resp.status());
    console.error('body:', body);
    console.error('redirect chain:', JSON.stringify(redirectURLs, null, 2));
    console.error('cookies:', JSON.stringify(cookies, null, 2));
    console.error('accessToken present:', !!accessToken);
  }

  expect(resp.status()).toBe(200);
  const body = await resp.json();
  expect(body.email).toBe(TEST_EMAIL);
});

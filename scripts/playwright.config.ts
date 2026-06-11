import { defineConfig } from '@playwright/test';

// Playwright config for the SSO smoke. Uses Caddy's self-signed certs
// (tls internal in Caddyfile.sso), so ignoreHTTPSErrors must be on.
// baseURL is the dashboard host; the test references the IdP by absolute URL.
export default defineConfig({
  testDir: '.',
  use: {
    headless: true,
    ignoreHTTPSErrors: true,
    baseURL: 'https://inferia.local',
    trace: 'retain-on-failure',
  },
  reporter: 'list',
  timeout: 60_000,
  expect: {
    timeout: 10_000,
  },
});

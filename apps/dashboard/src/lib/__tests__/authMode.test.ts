import { afterEach, describe, expect, it, vi } from "vitest";
import { authProvider, isExternalAuthMode } from "@/lib/authMode";

afterEach(() => {
  vi.unstubAllEnvs();
  delete (window as unknown as { __RUNTIME_CONFIG__?: unknown }).__RUNTIME_CONFIG__;
});

function setRuntimeConfig(cfg: Record<string, unknown>): void {
  (window as unknown as { __RUNTIME_CONFIG__?: unknown }).__RUNTIME_CONFIG__ = cfg;
}

describe("authProvider", () => {
  it("returns the VITE_AUTH_PROVIDER value when set", () => {
    vi.stubEnv("VITE_AUTH_PROVIDER", "inferiaauth");
    expect(authProvider()).toBe("inferiaauth");
  });

  it("returns 'local' when VITE_AUTH_PROVIDER is empty/unset", () => {
    vi.stubEnv("VITE_AUTH_PROVIDER", "");
    // Empty string is falsy so the || "local" fallback fires.
    expect(authProvider()).toBe("local");
  });

  it("prefers window.__RUNTIME_CONFIG__.AUTH_PROVIDER over the baked env", () => {
    // Build baked 'local' but the runtime config (write-dashboard-config) says
    // inferiaauth — the runtime value must win so one image serves any mode.
    vi.stubEnv("VITE_AUTH_PROVIDER", "local");
    setRuntimeConfig({ AUTH_PROVIDER: "inferiaauth" });
    expect(authProvider()).toBe("inferiaauth");
  });

  it("falls back to the baked env when runtime AUTH_PROVIDER is empty", () => {
    vi.stubEnv("VITE_AUTH_PROVIDER", "oidc");
    setRuntimeConfig({ AUTH_PROVIDER: "" });
    expect(authProvider()).toBe("oidc");
  });

  it("ignores a non-string runtime AUTH_PROVIDER", () => {
    vi.stubEnv("VITE_AUTH_PROVIDER", "local");
    setRuntimeConfig({ AUTH_PROVIDER: 123 as unknown as string });
    expect(authProvider()).toBe("local");
  });

  it("trims a padded runtime AUTH_PROVIDER", () => {
    vi.stubEnv("VITE_AUTH_PROVIDER", "local");
    setRuntimeConfig({ AUTH_PROVIDER: "  inferiaauth  " });
    expect(authProvider()).toBe("inferiaauth");
  });
});

describe("isExternalAuthMode with runtime config", () => {
  it("is true when runtime config selects inferiaauth over baked local", () => {
    vi.stubEnv("VITE_AUTH_PROVIDER", "local");
    setRuntimeConfig({ AUTH_PROVIDER: "inferiaauth" });
    expect(isExternalAuthMode()).toBe(true);
  });
});

describe("isExternalAuthMode", () => {
  it("returns true for 'inferiaauth'", () => {
    vi.stubEnv("VITE_AUTH_PROVIDER", "inferiaauth");
    expect(isExternalAuthMode()).toBe(true);
  });

  it("returns true for 'oidc'", () => {
    vi.stubEnv("VITE_AUTH_PROVIDER", "oidc");
    expect(isExternalAuthMode()).toBe(true);
  });

  it("returns true for the legacy 'external' alias", () => {
    vi.stubEnv("VITE_AUTH_PROVIDER", "external");
    expect(isExternalAuthMode()).toBe(true);
  });

  it("returns false for 'local'", () => {
    vi.stubEnv("VITE_AUTH_PROVIDER", "local");
    expect(isExternalAuthMode()).toBe(false);
  });

  it("returns false when VITE_AUTH_PROVIDER is unset (empty string)", () => {
    vi.stubEnv("VITE_AUTH_PROVIDER", "");
    expect(isExternalAuthMode()).toBe(false);
  });

  it("returns false for an unrecognised/garbage value", () => {
    vi.stubEnv("VITE_AUTH_PROVIDER", "saml");
    expect(isExternalAuthMode()).toBe(false);
  });
});

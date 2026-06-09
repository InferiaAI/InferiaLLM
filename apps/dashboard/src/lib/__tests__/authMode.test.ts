import { afterEach, describe, expect, it, vi } from "vitest";
import { authProvider, isExternalAuthMode } from "@/lib/authMode";

afterEach(() => {
  vi.unstubAllEnvs();
});

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

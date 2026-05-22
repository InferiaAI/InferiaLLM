import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  consumeAccessTokenFragment,
  logout,
  startExternalLogin,
} from "@/services/authService";
import * as tokenStore from "@/lib/tokenStore";

/**
 * jsdom's `window.location` is not redefinable property-by-property, but
 * `window.history.replaceState` accepts a relative URL and updates location.
 * We use that to drive `hash`, `pathname`, and `search` for each test.
 *
 * `originalReplaceState` is captured at module load so that test fixtures can
 * mutate the URL even while production code's call to `history.replaceState`
 * is spied/mocked.
 */
const originalReplaceState = window.history.replaceState.bind(window.history);

function setLocation(hash: string, pathname = "/", search = ""): void {
  const url = pathname + search + (hash ? (hash.startsWith("#") ? hash : `#${hash}`) : "");
  originalReplaceState(null, "", url);
}

describe("startExternalLogin", () => {
  // jsdom's `Location.assign` is read-only and non-configurable, so we
  // swap the whole `window.location` with a stub for the duration of the
  // test, then restore the real Location afterwards.
  const originalLocation = window.location;
  let assignMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    assignMock = vi.fn();
    Object.defineProperty(window, "location", {
      configurable: true,
      writable: true,
      value: {
        ...originalLocation,
        assign: assignMock,
      },
    });
  });

  afterEach(() => {
    Object.defineProperty(window, "location", {
      configurable: true,
      writable: true,
      value: originalLocation,
    });
  });

  it("redirects the browser to /auth/start", () => {
    startExternalLogin();
    expect(assignMock).toHaveBeenCalledTimes(1);
    expect(assignMock).toHaveBeenCalledWith("/auth/start");
  });
});

describe("consumeAccessTokenFragment", () => {
  let replaceStateSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    replaceStateSpy = vi
      .spyOn(window.history, "replaceState")
      .mockImplementation(() => {});
  });

  afterEach(() => {
    replaceStateSpy.mockRestore();
    setLocation("");
  });

  it("returns null when the hash is empty", () => {
    setLocation("");
    expect(consumeAccessTokenFragment()).toBeNull();
    expect(replaceStateSpy).not.toHaveBeenCalled();
  });

  it("returns null when the fragment has no access_token", () => {
    setLocation("#state=foo&other=bar");
    expect(consumeAccessTokenFragment()).toBeNull();
    expect(replaceStateSpy).not.toHaveBeenCalled();
  });

  it("returns null for a hash that is just '#'", () => {
    setLocation("#");
    expect(consumeAccessTokenFragment()).toBeNull();
    expect(replaceStateSpy).not.toHaveBeenCalled();
  });

  it("returns the token and scrubs the fragment when present", () => {
    setLocation("#access_token=abc.def.ghi", "/dashboard", "?foo=1");
    expect(consumeAccessTokenFragment()).toBe("abc.def.ghi");
    expect(replaceStateSpy).toHaveBeenCalledTimes(1);
    expect(replaceStateSpy).toHaveBeenCalledWith(null, "", "/dashboard?foo=1");
  });

  it("rejects absurdly long tokens (>8192 chars)", () => {
    const longToken = "a".repeat(8193);
    setLocation(`#access_token=${longToken}`);
    expect(consumeAccessTokenFragment()).toBeNull();
    expect(replaceStateSpy).not.toHaveBeenCalled();
  });

  it("accepts tokens at the 8192 boundary", () => {
    const boundary = "a".repeat(8192);
    setLocation(`#access_token=${boundary}`);
    expect(consumeAccessTokenFragment()).toBe(boundary);
    expect(replaceStateSpy).toHaveBeenCalledTimes(1);
  });

  it("returns null when access_token param is empty", () => {
    setLocation("#access_token=");
    expect(consumeAccessTokenFragment()).toBeNull();
    expect(replaceStateSpy).not.toHaveBeenCalled();
  });

  it("ignores a fragment with only delimiters", () => {
    setLocation("#&&=&");
    expect(consumeAccessTokenFragment()).toBeNull();
  });
});

describe("logout", () => {
  const originalLocation = window.location;
  const originalFetch = window.fetch;
  let assignMock: ReturnType<typeof vi.fn>;
  let fetchMock: ReturnType<typeof vi.fn>;
  let clearSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    assignMock = vi.fn();
    Object.defineProperty(window, "location", {
      configurable: true,
      writable: true,
      value: {
        ...originalLocation,
        origin: "https://inferia.local",
        assign: assignMock,
      },
    });
    fetchMock = vi.fn().mockResolvedValue({ ok: true });
    window.fetch = fetchMock as unknown as typeof window.fetch;
    clearSpy = vi.spyOn(tokenStore, "clearToken");
  });

  afterEach(() => {
    Object.defineProperty(window, "location", {
      configurable: true,
      writable: true,
      value: originalLocation,
    });
    window.fetch = originalFetch;
    clearSpy.mockRestore();
    vi.unstubAllEnvs();
  });

  it("local mode: clears the token store, POSTs /auth/logout, then redirects to /login", async () => {
    vi.stubEnv("VITE_AUTH_PROVIDER", "local");
    await logout();
    expect(clearSpy).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith("/auth/logout", {
      method: "POST",
      credentials: "include",
    });
    expect(assignMock).toHaveBeenCalledWith("/login");
  });

  it("external mode: redirects to the IdP /logout with post_logout_redirect_uri", async () => {
    vi.stubEnv("VITE_AUTH_PROVIDER", "external");
    vi.stubEnv("VITE_EXTERNAL_AUTH_URL", "https://auth.inferia.local");
    await logout();
    expect(clearSpy).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith("/auth/logout", {
      method: "POST",
      credentials: "include",
    });
    expect(assignMock).toHaveBeenCalledTimes(1);
    const dest = assignMock.mock.calls[0][0] as string;
    expect(dest).toMatch(/^https:\/\/auth\.inferia\.local\/logout\?/);
    const url = new URL(dest);
    expect(url.searchParams.get("post_logout_redirect_uri")).toBe(
      "https://inferia.local/login",
    );
  });

  it("does not throw when /auth/logout fails on the network", async () => {
    vi.stubEnv("VITE_AUTH_PROVIDER", "local");
    fetchMock.mockRejectedValueOnce(new Error("network"));
    await expect(logout()).resolves.toBeUndefined();
    // User-facing redirect must still happen.
    expect(assignMock).toHaveBeenCalledWith("/login");
    // Token store must still be cleared even when the audit POST fails.
    expect(clearSpy).toHaveBeenCalledTimes(1);
  });

  it("external mode without VITE_EXTERNAL_AUTH_URL falls back to /login", async () => {
    vi.stubEnv("VITE_AUTH_PROVIDER", "external");
    vi.stubEnv("VITE_EXTERNAL_AUTH_URL", "");
    await logout();
    expect(assignMock).toHaveBeenCalledWith("/login");
    expect(clearSpy).toHaveBeenCalledTimes(1);
  });

  it("external mode with malformed VITE_EXTERNAL_AUTH_URL falls back to /login", async () => {
    vi.stubEnv("VITE_AUTH_PROVIDER", "external");
    vi.stubEnv("VITE_EXTERNAL_AUTH_URL", "not a url");
    await logout();
    expect(assignMock).toHaveBeenCalledWith("/login");
    expect(clearSpy).toHaveBeenCalledTimes(1);
  });
});

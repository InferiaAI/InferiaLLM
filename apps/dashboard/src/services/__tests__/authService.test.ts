import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  consumeAccessTokenFragment,
  startExternalLogin,
} from "@/services/authService";

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

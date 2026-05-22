import { render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useTokenFragmentConsumer } from "@/hooks/useTokenFragmentConsumer";
import { clearToken, getToken } from "@/lib/tokenStore";

const originalReplaceState = window.history.replaceState.bind(window.history);

function setHash(hash: string): void {
  originalReplaceState(null, "", `/${hash ? `#${hash}` : ""}`);
}

function Harness(): null {
  useTokenFragmentConsumer();
  return null;
}

describe("useTokenFragmentConsumer", () => {
  beforeEach(() => clearToken());
  afterEach(() => {
    setHash("");
    vi.restoreAllMocks();
  });

  it("populates tokenStore from #access_token=<jwt> on mount", () => {
    setHash("access_token=jwt-from-redirect");
    render(<Harness />);
    expect(getToken()).toBe("jwt-from-redirect");
  });

  it("does nothing when the fragment is empty", () => {
    setHash("");
    render(<Harness />);
    expect(getToken()).toBeNull();
  });

  it("does nothing when the fragment has no access_token", () => {
    setHash("error=access_denied&state=abc");
    render(<Harness />);
    expect(getToken()).toBeNull();
  });
});

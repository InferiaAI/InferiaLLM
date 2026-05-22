import { afterEach, describe, expect, it } from "vitest";
import {
  clearToken,
  getToken,
  setAccessToken,
  setToken,
} from "@/lib/tokenStore";

describe("tokenStore.setAccessToken", () => {
  afterEach(() => clearToken());

  it("is an alias for setToken (writes the in-memory access token)", () => {
    setAccessToken("abc.def.ghi");
    expect(getToken()).toBe("abc.def.ghi");
  });

  it("accepts null to clear the in-memory access token", () => {
    setToken("seeded");
    setAccessToken(null);
    expect(getToken()).toBeNull();
  });

  it("does not touch sessionStorage when called with a JWT", () => {
    const before = sessionStorage.length;
    setAccessToken("abc.def.ghi");
    expect(sessionStorage.length).toBe(before);
  });
});

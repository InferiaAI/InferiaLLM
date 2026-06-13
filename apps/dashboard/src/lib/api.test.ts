/**
 * Tests for api.ts URL helpers and constants.
 *
 * API_GATEWAY_URL is a module-level const captured at import time, so each
 * test group that needs a different value must call vi.resetModules() and
 * re-import api.ts dynamically after setting window.__RUNTIME_CONFIG__.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ── helpers ────────────────────────────────────────────────────────────────

function setRuntimeConfig(cfg: Record<string, unknown>): void {
    (window as unknown as { __RUNTIME_CONFIG__?: unknown }).__RUNTIME_CONFIG__ = cfg;
}

function clearRuntimeConfig(): void {
    delete (window as unknown as { __RUNTIME_CONFIG__?: unknown }).__RUNTIME_CONFIG__;
}

/** Dynamically import api.ts AFTER setting window.__RUNTIME_CONFIG__. */
async function importApi() {
    const mod = await import("./api");
    return mod;
}

// ── SIDECAR_URL must NOT be exported ───────────────────────────────────────

describe("SIDECAR_URL", () => {
    afterEach(() => {
        vi.resetModules();
        clearRuntimeConfig();
    });

    it("is NOT exported from api.ts (zero external consumers)", async () => {
        const mod = await importApi();
        expect((mod as Record<string, unknown>)["SIDECAR_URL"]).toBeUndefined();
    });
});

// ── COMPUTE_URL ends with /v1, not /api/v1 ─────────────────────────────────

describe("COMPUTE_URL", () => {
    afterEach(() => {
        vi.resetModules();
        clearRuntimeConfig();
    });

    it("ends with /v1 (not /api/v1) for default localhost base", async () => {
        vi.resetModules();
        clearRuntimeConfig();
        const mod = await importApi();
        expect(mod.COMPUTE_URL).toMatch(/\/v1$/);
        expect(mod.COMPUTE_URL).not.toContain("/api/v1");
    });

    it("ends with /v1 when API_GATEWAY_URL is relative /api", async () => {
        vi.resetModules();
        setRuntimeConfig({ API_GATEWAY_URL: "/api" });
        const mod = await importApi();
        expect(mod.COMPUTE_URL).toBe("/api/v1");
    });

    it("ends with /v1 when API_GATEWAY_URL is absolute http://host:8000", async () => {
        vi.resetModules();
        setRuntimeConfig({ API_GATEWAY_URL: "http://host:8000" });
        const mod = await importApi();
        expect(mod.COMPUTE_URL).toBe("http://host:8000/v1");
    });
});

// ── toWsUrl ────────────────────────────────────────────────────────────────

describe("toWsUrl — relative API_GATEWAY_URL (/api)", () => {
    beforeEach(() => {
        vi.resetModules();
        setRuntimeConfig({ API_GATEWAY_URL: "/api" });
    });

    afterEach(() => {
        vi.resetModules();
        clearRuntimeConfig();
    });

    /**
     * jsdom default origin is http://localhost/ (set in vitest.config.ts).
     * A relative /api base + http origin → ws://
     */
    it("builds ws:// URL against http page origin (localhost)", async () => {
        const mod = await importApi();
        const result = mod.toWsUrl("/v1/admin/workers/n1/logs");
        expect(result).toBe("ws://localhost/api/v1/admin/workers/n1/logs");
    });

    it("starts with ws://localhost/api/ for http origin", async () => {
        const mod = await importApi();
        const result = mod.toWsUrl("/v1/admin/workers/n1/logs");
        expect(result.startsWith("ws://localhost/api/")).toBe(true);
    });
});

describe("toWsUrl — relative API_GATEWAY_URL with https origin", () => {
    let origLocation: Location;

    beforeEach(() => {
        vi.resetModules();
        setRuntimeConfig({ API_GATEWAY_URL: "/api" });
        // Stub window.location to simulate https page
        origLocation = window.location;
        Object.defineProperty(window, "location", {
            configurable: true,
            writable: true,
            value: {
                ...origLocation,
                origin: "https://app.example.com",
                protocol: "https:",
                host: "app.example.com",
                hostname: "app.example.com",
                port: "",
                pathname: "/",
                href: "https://app.example.com/",
            },
        });
    });

    afterEach(() => {
        vi.resetModules();
        clearRuntimeConfig();
        Object.defineProperty(window, "location", {
            configurable: true,
            writable: true,
            value: origLocation,
        });
    });

    it("builds wss:// URL against https page origin", async () => {
        const mod = await importApi();
        const result = mod.toWsUrl("/v1/admin/workers/n1/logs");
        expect(result).toBe("wss://app.example.com/api/v1/admin/workers/n1/logs");
    });
});

describe("toWsUrl — relative API_GATEWAY_URL with http://localhost:3000 origin", () => {
    let origLocation: Location;

    beforeEach(() => {
        vi.resetModules();
        setRuntimeConfig({ API_GATEWAY_URL: "/api" });
        origLocation = window.location;
        Object.defineProperty(window, "location", {
            configurable: true,
            writable: true,
            value: {
                ...origLocation,
                origin: "http://localhost:3000",
                protocol: "http:",
                host: "localhost:3000",
                hostname: "localhost",
                port: "3000",
                pathname: "/",
                href: "http://localhost:3000/",
            },
        });
    });

    afterEach(() => {
        vi.resetModules();
        clearRuntimeConfig();
        Object.defineProperty(window, "location", {
            configurable: true,
            writable: true,
            value: origLocation,
        });
    });

    it("starts with ws://localhost:3000/api/ for http://localhost:3000 origin", async () => {
        const mod = await importApi();
        const result = mod.toWsUrl("/v1/admin/workers/n1/logs");
        expect(result.startsWith("ws://localhost:3000/api/")).toBe(true);
    });

    it("full path correct for http://localhost:3000", async () => {
        const mod = await importApi();
        const result = mod.toWsUrl("/v1/admin/workers/n1/logs");
        expect(result).toBe("ws://localhost:3000/api/v1/admin/workers/n1/logs");
    });
});

describe("toWsUrl — absolute http API_GATEWAY_URL", () => {
    beforeEach(() => {
        vi.resetModules();
        setRuntimeConfig({ API_GATEWAY_URL: "http://host:8000" });
    });

    afterEach(() => {
        vi.resetModules();
        clearRuntimeConfig();
    });

    it("produces ws:// URL from http base", async () => {
        const mod = await importApi();
        const result = mod.toWsUrl("/v1/x");
        expect(result).toBe("ws://host:8000/v1/x");
    });
});

describe("toWsUrl — absolute https API_GATEWAY_URL", () => {
    beforeEach(() => {
        vi.resetModules();
        setRuntimeConfig({ API_GATEWAY_URL: "https://host" });
    });

    afterEach(() => {
        vi.resetModules();
        clearRuntimeConfig();
    });

    it("produces wss:// URL from https base", async () => {
        const mod = await importApi();
        const result = mod.toWsUrl("/v1/x");
        expect(result).toBe("wss://host/v1/x");
    });
});

describe("toWsUrl — trailing slash in API_GATEWAY_URL", () => {
    beforeEach(() => {
        vi.resetModules();
        setRuntimeConfig({ API_GATEWAY_URL: "/api/" });
    });

    afterEach(() => {
        vi.resetModules();
        clearRuntimeConfig();
    });

    it("no double slash — /api/ + /v1/x → .../api/v1/x", async () => {
        const mod = await importApi();
        const result = mod.toWsUrl("/v1/x");
        expect(result).not.toContain("//v1");
        expect(result).toMatch(/\/api\/v1\/x$/);
    });
});

describe("toWsUrl — absolute http base with trailing slash", () => {
    beforeEach(() => {
        vi.resetModules();
        setRuntimeConfig({ API_GATEWAY_URL: "http://host:8000/" });
    });

    afterEach(() => {
        vi.resetModules();
        clearRuntimeConfig();
    });

    it("no double slash for absolute base with trailing slash", async () => {
        const mod = await importApi();
        const result = mod.toWsUrl("/v1/x");
        expect(result).not.toContain("//v1");
        expect(result).toBe("ws://host:8000/v1/x");
    });
});

describe("toWsUrl — default localhost:8000 base (no runtime config)", () => {
    beforeEach(() => {
        vi.resetModules();
        clearRuntimeConfig();
    });

    afterEach(() => {
        vi.resetModules();
        clearRuntimeConfig();
    });

    it("uses ws://localhost:8000 when no runtime config set", async () => {
        const mod = await importApi();
        const result = mod.toWsUrl("/v1/workers/n1/logs");
        expect(result).toBe("ws://localhost:8000/v1/workers/n1/logs");
    });
});

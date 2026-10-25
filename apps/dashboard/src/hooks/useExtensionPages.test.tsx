import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, renderHook, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { get: vi.fn() } };
});
vi.mock("@/context/AuthContext", () => ({ useAuth: () => ({ user: { org_id: "org-1" } }) }));

import { api } from "@/lib/api";
import ExtensionPage from "@/pages/ExtensionPage";
import { EXTENSION_PAGE_CONTRACT, extensionPagePath, useExtensionPages } from "./useExtensionPages";

const get = api.get as unknown as ReturnType<typeof vi.fn>;

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

const spend = {
  plugin: "spend", id: "spend", title: "Spend", icon: "wallet", section: "monitor",
  contract: EXTENSION_PAGE_CONTRACT, script: "/ext/_pages/spend/spend.js",
};

afterEach(() => get.mockReset());

describe("useExtensionPages", () => {
  it("lists the pages the gateway offers", async () => {
    get.mockResolvedValue({ data: { contract: 1, pages: [spend] } });
    const { result } = renderHook(() => useExtensionPages(), { wrapper });
    await waitFor(() => expect(result.current.pages).toEqual([spend]));
    expect(get).toHaveBeenCalledWith("/ext/_pages");
  });

  it("drops pages written for another page contract", async () => {
    get.mockResolvedValue({ data: { contract: 2, pages: [spend, { ...spend, id: "next", contract: 2 }] } });
    const { result } = renderHook(() => useExtensionPages(), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.pages.map((p) => p.id)).toEqual(["spend"]);
  });

  it("is empty when no extension is installed", async () => {
    get.mockRejectedValue({ response: { status: 404 } });
    const { result } = renderHook(() => useExtensionPages(), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.pages).toEqual([]);
  });

  it("treats any other failure as an error, not as nothing installed", async () => {
    get.mockRejectedValue({ response: { status: 502 } });
    const { result } = renderHook(() => useExtensionPages(), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.pages).toEqual([]);
  });

  it("links each page under /dashboard/ext", () => {
    expect(extensionPagePath(spend)).toBe("/dashboard/ext/spend/spend");
  });
});

describe("ExtensionPage", () => {
  it("says a page that is not offered was not found, without fetching any script", async () => {
    get.mockResolvedValue({ data: { contract: 1, pages: [spend] } });
    render(
      <MemoryRouter initialEntries={["/dashboard/ext/other/page"]}>
        <Routes><Route path="/dashboard/ext/:plugin/:page" element={<ExtensionPage />} /></Routes>
      </MemoryRouter>,
      { wrapper },
    );
    expect(await screen.findByText("Page not found")).toBeTruthy();
    expect(get).toHaveBeenCalledTimes(1); // the listing only
  });
});

describe("ExtensionPage when the listing fails", () => {
  it("says the page could not be loaded rather than not found", async () => {
    get.mockRejectedValue({ response: { status: 502 } });
    render(
      <MemoryRouter initialEntries={["/dashboard/ext/spend/spend"]}>
        <Routes><Route path="/dashboard/ext/:plugin/:page" element={<ExtensionPage />} /></Routes>
      </MemoryRouter>,
      { wrapper },
    );
    expect(await screen.findByText("This page could not be loaded")).toBeTruthy();
  });
});

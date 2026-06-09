import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ExternalIdentityGuard } from "@/components/ExternalIdentityGuard";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

const NOTICE_HEADING = /managed by your identity provider/i;
const CHILD_TEXT = "Child content";

function ChildContent() {
  return <div>{CHILD_TEXT}</div>;
}

describe("ExternalIdentityGuard — external auth modes", () => {
  it("shows the IdP notice and hides children when VITE_AUTH_PROVIDER=inferiaauth", async () => {
    vi.stubEnv("VITE_AUTH_PROVIDER", "inferiaauth");
    // Re-import so the module picks up the stubbed env value
    const { ExternalIdentityGuard: Guard } = await import("@/components/ExternalIdentityGuard");
    render(
      <Guard>
        <ChildContent />
      </Guard>
    );
    expect(screen.getByRole("heading", { name: NOTICE_HEADING })).toBeInTheDocument();
    expect(screen.queryByText(CHILD_TEXT)).not.toBeInTheDocument();
  });

  it("shows the IdP notice and hides children when VITE_AUTH_PROVIDER=oidc", async () => {
    vi.stubEnv("VITE_AUTH_PROVIDER", "oidc");
    const { ExternalIdentityGuard: Guard } = await import("@/components/ExternalIdentityGuard");
    render(
      <Guard>
        <ChildContent />
      </Guard>
    );
    expect(screen.getByRole("heading", { name: NOTICE_HEADING })).toBeInTheDocument();
    expect(screen.queryByText(CHILD_TEXT)).not.toBeInTheDocument();
  });

  it("shows the IdP notice for the legacy 'external' alias", async () => {
    vi.stubEnv("VITE_AUTH_PROVIDER", "external");
    const { ExternalIdentityGuard: Guard } = await import("@/components/ExternalIdentityGuard");
    render(
      <Guard>
        <ChildContent />
      </Guard>
    );
    expect(screen.getByRole("heading", { name: NOTICE_HEADING })).toBeInTheDocument();
    expect(screen.queryByText(CHILD_TEXT)).not.toBeInTheDocument();
  });
});

describe("ExternalIdentityGuard — local mode", () => {
  it("renders children when VITE_AUTH_PROVIDER=local", async () => {
    vi.stubEnv("VITE_AUTH_PROVIDER", "local");
    const { ExternalIdentityGuard: Guard } = await import("@/components/ExternalIdentityGuard");
    render(
      <Guard>
        <ChildContent />
      </Guard>
    );
    expect(screen.getByText(CHILD_TEXT)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: NOTICE_HEADING })).not.toBeInTheDocument();
  });

  it("renders children when VITE_AUTH_PROVIDER is unset (defaults to local)", async () => {
    vi.stubEnv("VITE_AUTH_PROVIDER", "");
    const { ExternalIdentityGuard: Guard } = await import("@/components/ExternalIdentityGuard");
    render(
      <Guard>
        <ChildContent />
      </Guard>
    );
    expect(screen.getByText(CHILD_TEXT)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: NOTICE_HEADING })).not.toBeInTheDocument();
  });
});

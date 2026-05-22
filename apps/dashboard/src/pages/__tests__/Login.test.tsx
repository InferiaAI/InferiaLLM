import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Mock the auth-redirect helper so we can assert it's invoked without
// touching window.location. `vi.hoisted` lets us share the spy with the
// hoisted `vi.mock` factory below.
const { startExternalLoginMock } = vi.hoisted(() => ({
  startExternalLoginMock: vi.fn(),
}));
vi.mock("@/services/authService", async () => {
  const actual = await vi.importActual<typeof import("@/services/authService")>(
    "@/services/authService",
  );
  return {
    ...actual,
    startExternalLogin: startExternalLoginMock,
  };
});

// Stub AuthContext so Login renders without a real provider.
vi.mock("@/context/AuthContext", () => ({
  useAuth: () => ({
    login: vi.fn(),
    user: null,
    isLoading: false,
    isAuthenticated: false,
    logout: vi.fn(),
    refreshUser: vi.fn(),
    organizations: [],
    hasPermission: () => false,
  }),
}));

// Stub api so the local form doesn't actually fire HTTP.
vi.mock("@/lib/api", () => ({
  default: { post: vi.fn() },
}));

import Login from "@/pages/Login";

function renderLogin() {
  return render(
    <MemoryRouter>
      <Login />
    </MemoryRouter>,
  );
}

describe("Login page — VITE_AUTH_PROVIDER=external", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_AUTH_PROVIDER", "external");
    startExternalLoginMock.mockReset();
  });
  afterEach(() => vi.unstubAllEnvs());

  it("renders the 'Sign in with Inferia' button", () => {
    renderLogin();
    expect(
      screen.getByRole("button", { name: /sign in with inferia/i }),
    ).toBeInTheDocument();
  });

  it("hides the email/password inputs by default (collapsed in <details>)", () => {
    renderLogin();
    // The administrator-fallback form lives inside a collapsed <details>
    // element, so although it's in the DOM, the inputs are not visible.
    const emailInput = screen.queryByLabelText(/work email/i);
    // jsdom doesn't compute layout, but the parent <details> has no `open`
    // attribute by default, so toBeVisible should reflect that.
    expect(emailInput).not.toBeNull();
    expect(emailInput).not.toBeVisible();
  });

  it("clicking the redirect button calls startExternalLogin", async () => {
    const user = userEvent.setup();
    renderLogin();
    await user.click(
      screen.getByRole("button", { name: /sign in with inferia/i }),
    );
    expect(startExternalLoginMock).toHaveBeenCalledTimes(1);
  });

  it("still exposes an administrator sign-in fallback via <details>", () => {
    renderLogin();
    expect(screen.getByText(/administrator sign in/i)).toBeInTheDocument();
  });
});

describe("Login page — VITE_AUTH_PROVIDER=local (or unset)", () => {
  afterEach(() => vi.unstubAllEnvs());

  it("renders the local credential form by default (env unset)", () => {
    vi.stubEnv("VITE_AUTH_PROVIDER", "");
    renderLogin();
    expect(screen.getByLabelText(/work email/i)).toBeVisible();
    expect(
      screen.queryByRole("button", { name: /sign in with inferia/i }),
    ).toBeNull();
  });

  it("renders the local credential form when VITE_AUTH_PROVIDER=local", () => {
    vi.stubEnv("VITE_AUTH_PROVIDER", "local");
    renderLogin();
    expect(screen.getByLabelText(/work email/i)).toBeVisible();
    expect(
      screen.queryByRole("button", { name: /sign in with inferia/i }),
    ).toBeNull();
  });

  it("falls back to the local form for garbage values of VITE_AUTH_PROVIDER", () => {
    vi.stubEnv("VITE_AUTH_PROVIDER", "EXTERNAL"); // case-sensitive, must match exactly
    renderLogin();
    expect(screen.getByLabelText(/work email/i)).toBeVisible();
    expect(
      screen.queryByRole("button", { name: /sign in with inferia/i }),
    ).toBeNull();
  });

  it("falls back to the local form for arbitrary garbage", () => {
    vi.stubEnv("VITE_AUTH_PROVIDER", "ldap-magic");
    renderLogin();
    expect(screen.getByLabelText(/work email/i)).toBeVisible();
    expect(
      screen.queryByRole("button", { name: /sign in with inferia/i }),
    ).toBeNull();
  });
});

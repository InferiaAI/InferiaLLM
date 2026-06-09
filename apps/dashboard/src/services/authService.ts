import api from "@/lib/api";
import * as tokenStore from "@/lib/tokenStore";
import { isExternalAuthMode } from "@/lib/authMode";

const { setToken } = tokenStore;

/** Maximum allowed length for an access token returned via URL fragment.
 *  Mirrors the gateway-side JWKSVerifier cap so a malformed redirect can't
 *  pump arbitrary-sized data into the in-memory token store. */
export const MAX_ACCESS_TOKEN_LENGTH = 8192;

/**
 * Kick the browser into the gateway-driven OAuth flow. The gateway will 302
 * through inferia-auth's `/oauth/authorize`, the user logs in there, and the
 * gateway redirects back to the dashboard with `#access_token=<jwt>`.
 */
export function startExternalLogin(): void {
  window.location.assign("/auth/start");
}

/**
 * Read `#access_token=<jwt>` from the current URL fragment, return the token,
 * and scrub the fragment from the address bar via `history.replaceState` so
 * the token doesn't leak into browser history, the `Referer` header on the
 * next navigation, or any analytics that read `location.href`.
 *
 * Returns `null` when the fragment is empty, missing the `access_token`
 * param, malformed, or carries a token over `MAX_ACCESS_TOKEN_LENGTH`.
 */
export function consumeAccessTokenFragment(): string | null {
  if (!window.location.hash) return null;
  const fragment = window.location.hash.startsWith("#")
    ? window.location.hash.slice(1)
    : window.location.hash;
  if (!fragment) return null;
  let params: URLSearchParams;
  try {
    params = new URLSearchParams(fragment);
  } catch {
    return null;
  }
  const token = params.get("access_token");
  if (!token) return null;
  if (token.length > MAX_ACCESS_TOKEN_LENGTH) return null;
  // Scrub before any handler can capture it.
  window.history.replaceState(
    null,
    "",
    window.location.pathname + window.location.search,
  );
  return token;
}

/**
 * Sign the user out of the dashboard.
 *
 * Order matters:
 * 1. Clear the in-memory access token first so nothing else can re-use it
 *    while the network request is still in flight.
 * 2. POST `/auth/logout` (best-effort — the gateway holds no server-side
 *    session state now that refresh lives in an httpOnly cookie, but the
 *    endpoint still writes an audit log entry). A network failure here
 *    must NOT block the user-facing redirect.
 * 3. In external mode, navigate to the IdP's `/logout?post_logout_redirect_uri=`
 *    so the SSO cookie at auth.* clears too. Otherwise drop the user on /login.
 */
export async function logout(): Promise<void> {
  tokenStore.clearToken();
  try {
    await fetch("/auth/logout", { method: "POST", credentials: "include" });
  } catch {
    // Network failure on the audit POST must not block the redirect.
  }

  const isExternal = isExternalAuthMode();
  const externalUrl = import.meta.env.VITE_EXTERNAL_AUTH_URL as
    | string
    | undefined;

  if (isExternal && externalUrl) {
    try {
      const dest = new URL("/logout", externalUrl);
      dest.searchParams.set(
        "post_logout_redirect_uri",
        window.location.origin + "/login",
      );
      window.location.assign(dest.toString());
      return;
    } catch {
      // VITE_EXTERNAL_AUTH_URL is malformed; fall through to the local
      // redirect rather than crashing the page during sign-out.
    }
  }

  window.location.assign("/login");
}

export interface RegisterRequest {
    email: string;
    password: string;
    organization_name?: string;
    invite_token?: string;
}

export interface InviteInfo {
    email: string;
    role: string;
    token: string;
    invite_link: string;
    status: string;
    expires_at: string;
}

export interface OrganizationBasicInfo {
    id: string;
    name: string;
    role: string;
}

export interface AuthResponse {
    access_token: string;
    refresh_token: string;
    token_type: string;
    expires_in: number;
    organizations?: OrganizationBasicInfo[];
}

export const authService = {
    login: async (credentials: any) => {
        const { data } = await api.post<AuthResponse>("/auth/login", credentials);
        return data;
    },
    register: async (credentials: RegisterRequest) => {
        const { data } = await api.post<AuthResponse>("/auth/register", credentials);
        return data;
    },
    registerInvite: async (payload: { token: string; password: string }) => {
        const { data } = await api.post<AuthResponse>("/auth/register-invite", payload);
        return data;
    },
    getInviteInfo: async (token: string) => {
        const { data } = await api.get<InviteInfo>(`/auth/invitations/${token}`);
        return data;
    },
    acceptInvite: async (token: string) => {
        const { data } = await api.post<AuthResponse>(`/auth/accept-invite?token=${token}`);
        if (data.access_token) {
            setToken(data.access_token);
        }
        return data;
    },
    switchOrg: async (orgId: string) => {
        const { data } = await api.post<AuthResponse>("/auth/switch-org", { org_id: orgId });
        return data;
    },
    getOrganizations: async (params?: { skip?: number; limit?: number }) => {
        const { data } = await api.get<OrganizationBasicInfo[]>("/auth/organizations", { params });
        return data;
    }
};

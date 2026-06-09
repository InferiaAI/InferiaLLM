// Auth mode resolution order:
//   1. Runtime config (window.__RUNTIME_CONFIG__.AUTH_PROVIDER) — written at
//      container start by `inferiallm write-dashboard-config` from the
//      VITE_AUTH_PROVIDER / AUTH_PROVIDER env. This lets a single SPA build serve
//      local / oidc / inferiaauth without rebaking the image.
//   2. Build-time fallback (import.meta.env.VITE_AUTH_PROVIDER) — for dev / when
//      the runtime config is absent.
//   3. "local".
// "local" = built-in user/password; "oidc"/"inferiaauth" = redirect to an
// external IdP. "external" is a deprecated alias for "inferiaauth".
export type AuthMode = "local" | "oidc" | "inferiaauth";

interface RuntimeConfig {
  AUTH_PROVIDER?: string;
}

function runtimeAuthProvider(): string {
  if (typeof window === "undefined") return "";
  const rc = (window as unknown as { __RUNTIME_CONFIG__?: RuntimeConfig })
    .__RUNTIME_CONFIG__;
  return rc && typeof rc.AUTH_PROVIDER === "string" ? rc.AUTH_PROVIDER.trim() : "";
}

export function authProvider(): string {
  return (
    runtimeAuthProvider() ||
    (import.meta.env.VITE_AUTH_PROVIDER as string) ||
    "local"
  );
}

/** True when login is delegated to an external IdP (oidc or inferiaauth). */
export function isExternalAuthMode(): boolean {
  const p = authProvider();
  return p === "oidc" || p === "inferiaauth" || p === "external";
}

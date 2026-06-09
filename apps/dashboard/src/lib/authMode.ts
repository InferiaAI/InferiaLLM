// Auth mode is baked in at build time via VITE_AUTH_PROVIDER.
// "local" = built-in user/password; "oidc"/"inferiaauth" = redirect to an
// external IdP. "external" is a deprecated alias for "inferiaauth".
export type AuthMode = "local" | "oidc" | "inferiaauth";

export function authProvider(): string {
  return (import.meta.env.VITE_AUTH_PROVIDER as string) || "local";
}

/** True when login is delegated to an external IdP (oidc or inferiaauth). */
export function isExternalAuthMode(): boolean {
  const p = authProvider();
  return p === "oidc" || p === "inferiaauth" || p === "external";
}

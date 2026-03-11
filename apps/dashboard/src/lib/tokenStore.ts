/**
 * In-memory token store.
 *
 * Tokens are held in a closure — never written to localStorage or
 * sessionStorage — so they cannot be stolen by XSS scripts that read
 * the Web Storage API.
 *
 * Trade-off: a page refresh clears the token and the user must
 * re-authenticate.  This is the standard security posture for
 * high-sensitivity admin dashboards.
 */

let _token: string | null = null;

export function getToken(): string | null {
  return _token;
}

export function setToken(token: string | null): void {
  _token = token;
}

export function clearToken(): void {
  _token = null;
}

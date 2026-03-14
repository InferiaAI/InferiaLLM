/**
 * Token store.
 *
 * Access tokens are kept in-memory (safe from XSS localStorage scraping).
 * Refresh tokens are stored in sessionStorage so they survive page
 * refreshes but are cleared when the browser tab closes.
 */

const RT_KEY = "_rt";

// ── Access token (in-memory) ────────────────────────────────────────

let _token: string | null = null;

export function getToken(): string | null {
  return _token;
}

export function setToken(token: string | null): void {
  _token = token;
}

export function clearToken(): void {
  _token = null;
  clearRefreshToken();
}

// ── Refresh token (sessionStorage) ──────────────────────────────────

export function getRefreshToken(): string | null {
  try {
    return sessionStorage.getItem(RT_KEY);
  } catch {
    return null;
  }
}

export function setRefreshToken(token: string | null): void {
  try {
    if (token) {
      sessionStorage.setItem(RT_KEY, token);
    } else {
      sessionStorage.removeItem(RT_KEY);
    }
  } catch {
    // sessionStorage unavailable (e.g. private browsing quota exceeded)
  }
}

export function clearRefreshToken(): void {
  try {
    sessionStorage.removeItem(RT_KEY);
  } catch {
    // ignore
  }
}

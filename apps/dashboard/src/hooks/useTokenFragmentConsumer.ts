import { useRef } from "react";

import { consumeAccessTokenFragment } from "@/services/authService";
import { setAccessToken } from "@/lib/tokenStore";

/**
 * Parse `#access_token=...` from the URL fragment, store it in the in-memory
 * token store, and scrub the fragment from the address bar so the token
 * doesn't leak into history, the `Referer` header, or any analytics that
 * read `location.href`.
 *
 * Runs synchronously during render (via a `useRef` initializer) so the token
 * is in place BEFORE `AuthProvider`'s init effect calls `getToken()`. React
 * fires children's effects before parents', so a parent `useEffect` would
 * run too late to seed the token.
 */
export function useTokenFragmentConsumer(): void {
  const consumed = useRef(false);
  if (!consumed.current) {
    consumed.current = true;
    const token = consumeAccessTokenFragment();
    if (token) {
      setAccessToken(token);
    }
  }
}

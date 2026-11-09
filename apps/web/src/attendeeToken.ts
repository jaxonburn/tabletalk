/** The attendee's enrollment token, shared by the attendee pages on this device. */
export const ATTENDEE_TOKEN_KEY = "tabletalk-attendee-token";

// Storage can be missing or blocked (privacy modes); the page then works for this session.
function tokenStore(): Storage | null {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null;
  }
}

export const readToken = () => tokenStore()?.getItem(ATTENDEE_TOKEN_KEY) ?? "";
export const storeToken = (token: string) =>
  tokenStore()?.setItem(ATTENDEE_TOKEN_KEY, token);
export const clearToken = () => tokenStore()?.removeItem(ATTENDEE_TOKEN_KEY);

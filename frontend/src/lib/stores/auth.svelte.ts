/**
 * JWT authentication store.
 *
 * Access tokens are kept in memory only — never written to localStorage or
 * sessionStorage — to prevent XSS token theft.  The httponly refresh cookie
 * is managed exclusively by the browser/server and is invisible to JS.
 */

const REFRESH_PATH = '/auth/jwt/refresh';
// Refresh 60 s before expiry to avoid clock-skew races.
const REFRESH_BUFFER_MS = 60_000;

class AuthStore {
  /** In-memory JWT access token. Cleared on logout or page close. */
  accessToken = $state<string | null>(null);

  /** Whether a silent token refresh is in progress. */
  refreshing = $state(false);

  /** Timer handle for proactive token rotation. */
  private _refreshTimer: ReturnType<typeof setTimeout> | null = null;

  // ── Token management ──────────────────────────────────────────────────

  /**
   * Store a new access token and schedule proactive rotation.
   * Call this after OAuth redirect capture.
   */
  setToken(token: string): void {
    this.accessToken = token;
    this._scheduleRefresh(token);
  }

  /** Clear the access token and cancel any pending rotation. */
  clearToken(): void {
    this.accessToken = null;
    if (this._refreshTimer !== null) {
      clearTimeout(this._refreshTimer);
      this._refreshTimer = null;
    }
  }

  /** True if a valid access token is held in memory. */
  get isAuthenticated(): boolean {
    return this.accessToken !== null;
  }

  // ── Silent refresh ────────────────────────────────────────────────────

  /**
   * Call `/auth/jwt/refresh` using the httponly refresh cookie.
   * On success, stores the new access token and returns it.
   * On failure (401 / network), clears the token and returns null.
   */
  async refresh(): Promise<string | null> {
    if (this.refreshing) return this.accessToken;
    this.refreshing = true;
    // Abort the request if the backend doesn't respond within 8 s.
    // Without a timeout, a hard reload while the backend is starting hangs
    // indefinitely, blocking the authChecked gate and keeping the loading screen up.
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 8_000);
    try {
      const res = await fetch(REFRESH_PATH, {
        method: 'POST',
        credentials: 'include',
        signal: controller.signal
      });
      if (!res.ok) {
        // 429 = rate-limited: the token is still valid; don't log the user out.
        // Any other non-2xx (401, 500 …) means the session is gone — clear it.
        if (res.status !== 429) {
          this.clearToken();
        }
        return null;
      }
      const data: { access_token: string } = await res.json();
      this.setToken(data.access_token);
      return data.access_token;
    } catch {
      this.clearToken();
      return null;
    } finally {
      clearTimeout(timeoutId);
      this.refreshing = false;
    }
  }

  // ── Proactive rotation ────────────────────────────────────────────────

  /**
   * Decode the JWT expiry claim (without verifying the signature — the server
   * verifies; we only need the `exp` timestamp to schedule rotation).
   */
  private _expiryMs(token: string): number | null {
    try {
      const payload = JSON.parse(atob(token.split('.')[1]));
      return typeof payload.exp === 'number' ? payload.exp * 1000 : null;
    } catch (e) {
      console.warn('[auth] Failed to decode JWT expiry — proactive refresh disabled:', e);
      return null;
    }
  }

  private _scheduleRefresh(token: string): void {
    if (this._refreshTimer !== null) clearTimeout(this._refreshTimer);
    const expMs = this._expiryMs(token);
    if (expMs === null) return;
    const delay = Math.max(0, expMs - Date.now() - REFRESH_BUFFER_MS);
    this._refreshTimer = setTimeout(() => this.refresh(), delay);
  }
}

export const auth = new AuthStore();

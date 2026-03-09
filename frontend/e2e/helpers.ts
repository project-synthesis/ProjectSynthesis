// frontend/e2e/helpers.ts
import type { Page } from '@playwright/test';

export const BACKEND_URL = 'http://localhost:8099';

export interface SeedAuthOptions {
  email?: string;
  githubLogin?: string;
  isNewUser?: boolean;
}

/**
 * Seed authentication state for E2E tests.
 *
 * The frontend auth store keeps the JWT in-memory only (never localStorage)
 * to prevent XSS token theft. On every page load the layout calls
 * `auth.refresh()` which hits `/auth/jwt/refresh` with an httponly cookie.
 *
 * Strategy:
 *  1. Call the test-only `/test/token` backend endpoint to issue a real JWT.
 *  2. Intercept the browser's `GET /auth/jwt/refresh` request and return that
 *     JWT so the layout's silent-refresh path populates `auth.accessToken`.
 *  3. Navigate to `/` and wait for the auth-checked state.
 *
 * Requires TESTING=true on the backend (set in playwright.config.ts webServer).
 */
export async function seedAuth(page: Page, opts: SeedAuthOptions = {}): Promise<string> {
  const { email = 'e2e@test.com', githubLogin = 'e2e-user', isNewUser = false } = opts;

  // 1. Obtain a real JWT from the test-only backend endpoint (Node.js fetch,
  //    runs outside the browser — no CORS concern).
  const resp = await fetch(`${BACKEND_URL}/test/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, github_login: githubLogin, is_new_user: isNewUser }),
  });

  if (!resp.ok) {
    throw new Error(
      `seedAuth: /test/token returned ${resp.status} — is TESTING=true set on the backend?`,
    );
  }

  const { access_token } = (await resp.json()) as { access_token: string };

  // 2. Intercept the silent-refresh call the layout makes on every page load.
  //    The handler fires once and then removes itself so subsequent real refreshes
  //    go through normally (unless seedAuth is called again).
  await page.route('**/auth/jwt/refresh', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ access_token }),
    });
  });

  // 3. Navigate to the app root — layout onMount will call auth.refresh(),
  //    hit our intercepted route, get the JWT, and call auth.setToken().
  //    Wait until the loading screen disappears (authChecked = true).
  await page.goto('/');
  // The layout shows a loading div while authChecked is false.
  // Once the silent refresh resolves the div is replaced by AuthGate or the workbench.
  await page.waitForFunction(() => {
    // The loading overlay contains the text "PROJECT SYNTHESIS" in a very dim span.
    // After auth resolves it is removed from the DOM.
    const loadingSpan = document.querySelector(
      '.h-screen.w-screen.flex.items-center.justify-center span',
    );
    return loadingSpan === null;
  });

  // Remove the route intercept so later navigations use the real refresh endpoint.
  await page.unroute('**/auth/jwt/refresh');

  return access_token;
}

/**
 * Clear all auth state by hard-navigating away and back, which discards the
 * in-memory token. Useful in afterEach hooks to isolate tests.
 */
export async function clearAuth(page: Page): Promise<void> {
  // Intercept the refresh so the layout gets a 401 and clearToken() is called.
  await page.route('**/auth/jwt/refresh', async (route) => {
    await route.fulfill({ status: 401, body: '' });
  });
  await page.reload();
  await page.unroute('**/auth/jwt/refresh');
}

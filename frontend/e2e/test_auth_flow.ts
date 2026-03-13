// frontend/e2e/test_auth_flow.ts
import { test, expect } from '@playwright/test';
import { seedAuth, clearAuth, BACKEND_URL } from './helpers';

test.afterEach(async ({ page }) => {
  await clearAuth(page);
});

test('auth gate renders when unauthenticated', async ({ page }) => {
  // Always intercept the refresh endpoint and return 401 immediately.
  // Relying on the real backend is fragile: the backend may be busy with
  // long-running pipeline stage timeouts, causing fetch() to hang since it
  // has no built-in timeout, which in turn prevents authChecked from being set.
  await page.route('**/auth/jwt/refresh', async (route) => {
    await route.fulfill({ status: 401, body: '' });
  });

  await page.goto('/');

  // Wait for the auth gate directly rather than waiting for the loading spinner
  // to disappear via a CSS-class selector.
  //
  // WHY NOT the loading-spinner selector:
  //   AuthGate.svelte wraps its content in:
  //     <div class="h-screen w-screen flex items-center justify-center ...">
  //   — the same classes used by the loading overlay.  After authChecked = true
  //   the loading screen is removed, but the auth gate renders with that same
  //   wrapper and contains its own <span> elements.  The selector
  //   ".h-screen.w-screen.flex.items-center.justify-center span" therefore still
  //   matches, the waitForFunction condition never becomes true, and the test
  //   times out on every run.
  await expect(page.locator('[data-testid="auth-gate"]')).toBeVisible({ timeout: 10_000 });

  await page.unroute('**/auth/jwt/refresh');

  // Workbench must NOT be rendered
  await expect(page.locator('nav[aria-label="Activity Bar"]')).not.toBeVisible();
});

test('workspace renders after auth injection', async ({ page }) => {
  await seedAuth(page, { isNewUser: false });
  // Workbench should appear
  await expect(page.locator('nav[aria-label="Activity Bar"]')).toBeVisible({ timeout: 15_000 });
  await expect(page.locator('nav[aria-label="Navigator"]')).toBeVisible();
  await expect(page.locator('footer[aria-label="Status Bar"]')).toBeVisible();
});

test('status bar shows user label after auth', async ({ page }) => {
  await seedAuth(page, { isNewUser: false, githubLogin: 'flow-a-user' });
  const statusBar = page.locator('footer[aria-label="Status Bar"]');
  await expect(statusBar).toBeVisible({ timeout: 15_000 });
  // The auth button shows user.label ?? github.username ?? 'JWT'
  // It must not show bare "JWT" as the only label (the full status bar has more content)
  const authBtn = page.locator('[data-testid="statusbar-auth"]');
  await expect(authBtn).toBeVisible();
  const text = await authBtn.textContent();
  expect(text?.trim()).not.toBe('JWT');
});

test('onboarding modal can be triggered', async ({ page }) => {
  // The layout shows the onboarding modal when ?auth_complete=1&new=1 is present in the URL
  // and the /auth/token exchange succeeds (OAuth callback path).
  // We obtain a real JWT from the test endpoint and intercept /auth/token so the
  // layout's callback handler calls auth.setToken() and sets showOnboarding=true.
  const tokenResp = await fetch(`${BACKEND_URL}/test/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      email: 'onboarding-test@e2e.com',
      github_login: 'onboarding-user',
      is_new_user: true,
    }),
  });
  const { access_token } = (await tokenResp.json()) as { access_token: string };

  // Intercept /auth/token (the OAuth callback token exchange endpoint)
  await page.route('**/auth/token', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ access_token }),
    });
  });

  // Navigate as if returning from GitHub OAuth with ?auth_complete=1&new=1
  await page.goto('/?auth_complete=1&new=1');

  // After the callback, authChecked = true and the workbench renders (not AuthGate).
  // The onboarding modal is rendered on top of the workbench.
  // The loading-spinner selector works here because the workbench does NOT use
  // "h-screen w-screen flex items-center justify-center" as its wrapper.
  await page.waitForFunction(
    () => {
      const loading = document.querySelector(
        '.h-screen.w-screen.flex.items-center.justify-center span',
      );
      return loading === null;
    },
    undefined,
    { timeout: 15_000 },
  );

  await page.unroute('**/auth/token');

  // The onboarding wizard should appear — heading "PROJECT SYNTHESIS" with
  // tagline "AI-Powered Prompt Engineering" and a 4-step flow.
  await expect(page.locator('#wizard-display-name')).toBeVisible({ timeout: 10_000 });
  // The wizard shows NEXT (step 1) and SKIP ALL buttons
  await expect(page.getByRole('button', { name: 'NEXT', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: /skip all/i })).toBeVisible();
});

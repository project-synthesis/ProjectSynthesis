// frontend/e2e/test_auth_flow.ts
import { test, expect } from '@playwright/test';
import { seedAuth, clearAuth, BACKEND_URL } from './helpers';

test.afterEach(async ({ page }) => {
  await clearAuth(page);
});

test('auth gate renders when unauthenticated', async ({ page }) => {
  await page.goto('/');
  // Wait for auth check to complete (loading overlay disappears)
  await page.waitForFunction(() => {
    const loadingSpan = document.querySelector(
      '.h-screen.w-screen.flex.items-center.justify-center span',
    );
    return loadingSpan === null;
  }, { timeout: 10_000 });
  // AuthGate must be visible — identified by data-testid="auth-gate"
  await expect(page.locator('[data-testid="auth-gate"]')).toBeVisible({ timeout: 5_000 });
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

  // Wait for auth to resolve (loading overlay gone)
  await page.waitForFunction(() => {
    const loading = document.querySelector(
      '.h-screen.w-screen.flex.items-center.justify-center span',
    );
    return loading === null;
  }, { timeout: 15_000 });

  await page.unroute('**/auth/token');

  // The onboarding modal should appear — heading is "Welcome to Project Synthesis"
  await expect(page.getByText(/Welcome to Project Synthesis/i)).toBeVisible({ timeout: 10_000 });
  // Input and buttons present
  await expect(page.locator('#onboarding-display-name')).toBeVisible();
  await expect(page.getByRole('button', { name: /get started/i })).toBeVisible();
});

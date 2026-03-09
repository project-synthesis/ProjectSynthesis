// frontend/e2e/test_github_flow.ts
import { test, expect } from '@playwright/test';
import { seedAuth, clearAuth } from './helpers';

test.beforeEach(async ({ page }) => {
  await seedAuth(page);
  await expect(page.locator('nav[aria-label="Activity Bar"]')).toBeVisible({ timeout: 15_000 });
});

test.afterEach(async ({ page }) => {
  await clearAuth(page);
});

test('GitHub section shows Not connected when no token', async ({ page }) => {
  // Intercept health to enable GitHub OAuth (needed for "Not connected" branch)
  await page.route('**/api/health', async (route) => {
    const resp = await route.fetch();
    const body = await resp.json();
    await route.fulfill({ json: { ...body, github_oauth_enabled: true } });
  });

  // Intercept /auth/github/me to return connected=false
  await page.route('**/auth/github/me', (route) => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ connected: false, login: null, avatar_url: null }),
    });
  });

  await page.reload();
  await expect(page.locator('nav[aria-label="Activity Bar"]')).toBeVisible({ timeout: 15_000 });

  // Open Settings panel via Ctrl+,
  await page.keyboard.press('Control+,');

  // Should show "Not connected" (not "GitHub App not configured")
  await expect(page.getByText(/not connected/i)).toBeVisible({ timeout: 10_000 });
});

test('simulated GitHub connected state shows username', async ({ page }) => {
  // Intercept the GitHub status endpoint to simulate connected state.
  // The layout calls fetchGitHubAuthStatus() which hits /auth/github/me.
  await page.route('**/auth/github/me', (route) => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        connected: true,
        login: 'mock-github-user',
        avatar_url: null,
      }),
    });
  });
  // Intercept the repos endpoint (layout fetches repos when connected)
  await page.route('**/api/github/repos', (route) => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    });
  });
  // Intercept the linked-repo endpoint (layout calls fetchLinkedRepo after repos)
  await page.route('**/api/github/repos/linked', (route) => {
    route.fulfill({ status: 404, body: '' });
  });

  await page.reload();
  await expect(page.locator('nav[aria-label="Activity Bar"]')).toBeVisible({ timeout: 15_000 });

  // Open settings panel
  await page.keyboard.press('Control+,');

  // NavigatorSettings shows github.username when connected
  await expect(page.getByText('mock-github-user')).toBeVisible({ timeout: 10_000 });
});

// frontend/e2e/test_pipeline.ts
import { test, expect } from '@playwright/test';
import { seedAuth, clearAuth } from './helpers';

test.beforeEach(async ({ page }) => {
  await seedAuth(page, { email: 'pipeline-test@test.com' });
  await expect(page.locator('nav[aria-label="Activity Bar"]')).toBeVisible({ timeout: 15_000 });
});

test.afterEach(async ({ page }) => {
  await clearAuth(page);
});

test('prompt textarea is visible and accepts input', async ({ page }) => {
  // PromptEdit renders a textarea with id="prompt-textarea"
  const textarea = page.locator('#prompt-textarea');
  await expect(textarea).toBeVisible({ timeout: 10_000 });
  await textarea.fill('Explain quantum entanglement to a 10-year-old.');
  await expect(textarea).toHaveValue('Explain quantum entanglement to a 10-year-old.');
});

test('submitting a prompt starts the pipeline', async ({ page }) => {
  const textarea = page.locator('#prompt-textarea');
  await textarea.fill('Write a concise executive summary.');

  // The forge button has data-testid="forge-button" and text "Synthesize"
  const forgeBtn = page.locator('[data-testid="forge-button"]');
  await expect(forgeBtn).toBeVisible();
  await forgeBtn.click();

  // Pipeline stage cards appear — StageCard renders data-testid="stage-card-{name}"
  // Possible stage names: explore, analyze, strategy, optimize, validate
  await expect(
    page.locator('[data-testid^="stage-card-"]').first(),
  ).toBeVisible({ timeout: 20_000 });
});

test('optimized result renders after pipeline completes', async ({ page }) => {
  const textarea = page.locator('#prompt-textarea');
  await textarea.fill('Summarize the French Revolution in three bullet points.');

  const forgeBtn = page.locator('[data-testid="forge-button"]');
  await forgeBtn.click();

  // Wait for pipeline to complete and show result.
  // The mock provider returns "Mock optimized prompt: always respond in bullet points."
  await expect(
    page.getByText('Mock optimized prompt').first(),
  ).toBeVisible({ timeout: 30_000 });
});

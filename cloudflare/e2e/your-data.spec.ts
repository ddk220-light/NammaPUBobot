import { test, expect } from '@playwright/test';
import { mockApp, guild } from './fixtures';

test('Setup offers a plain download of the community’s own data', async ({ page }) => {
  await mockApp(page);
  await page.goto(`/?community=${guild}&view=setup`);
  const block = page.locator('[data-your-data]');
  await expect(block.getByRole('heading', { name: 'Your data' })).toBeVisible();
  await expect(block).toContainText('JSON file');
  const link = block.getByRole('link', { name: 'Download community data' });
  await expect(link).toHaveAttribute('href', `/api/communities/${guild}/export`);
  await expect(link).toHaveAttribute('download', '');
  // Browser downloads bypass Playwright request routing; the response itself (file name,
  // contents, authorization) is covered by tests/operations.test.ts.
  const [download] = await Promise.all([page.waitForEvent('download'), link.click()]);
  expect(new URL(download.url()).pathname).toBe(`/api/communities/${guild}/export`);
});

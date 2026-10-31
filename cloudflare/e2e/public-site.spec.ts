import { test, expect } from '@playwright/test';
import { mockApp, guild } from './fixtures';
test('public content and setup guides work without JavaScript', async ({ browser }, info) => {
  const context = await browser.newContext({ javaScriptEnabled: false });
  const page = await context.newPage();
  await page.goto('http://127.0.0.1:4173/');
  await expect(page.getByRole('heading', { level: 1 })).toContainText('Free, open-source');
  await page.getByRole('link', { name: 'Age of Empires II', exact: true }).click();
  await expect(page.locator('main')).toContainText('captains confirm results');
  await expect(page.locator('meta[name="robots"]')).toHaveAttribute('content', 'noindex');
  await page.getByRole('link', { name: 'Age of Empires II setup guide', exact: true }).click();
  await expect(page.locator('main')).toContainText('/add');
  await page.screenshot({ path: info.outputPath('public-game-setup.png'), fullPage: true });
  await context.close();
});
test('four organizer destinations and theme choice preserve a community draft', async ({
  page,
}, info) => {
  await mockApp(page);
  await page.goto(`/app/?community=${guild}&view=queues`);
  await expect(page.locator('.workspace-tab')).toHaveCount(4);
  await page.getByRole('textbox', { name: 'Queue name', exact: true }).fill('Theme draft');
  await page.getByRole('button', { name: 'Account', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel('Theme').selectOption('dark');
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await expect(page.getByRole('textbox', { name: 'Queue name', exact: true })).toHaveValue(
    'Theme draft',
  );
  await expect(page.getByRole('button', { name: 'Account', exact: true })).toBeFocused();
  await page.screenshot({ path: info.outputPath('queues-dark.png'), fullPage: true });
});
test('legacy Overview links land on Queues', async ({ page }) => {
  await mockApp(page);
  await page.goto(`/?community=${guild}&view=overview`);
  await expect(page.getByRole('tab', { name: 'Queues', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  await expect(page).toHaveURL(/view=queues/);
});

test('search publishing is explicit, atomic and cleared when visibility becomes restricted', async ({
  page,
}) => {
  const state = await mockApp(page);
  await page.goto(`/app/?community=${guild}&view=setup`);
  const game = page.locator('.game-settings').first();
  await game.locator(':scope > summary').click();
  await game.getByLabel('Statistics visibility', { exact: true }).selectOption('public');
  const publishing = game.getByRole('checkbox', {
    name: 'Publish this leaderboard to search engines',
    exact: true,
  });
  await publishing.check();
  await page.locator('[data-edit="save"]').click();
  await expect(page.locator('#savebar')).toBeVisible();
  expect(state.saved).toHaveLength(0);
  await page
    .getByLabel('Public community display name', { exact: true })
    .fill('Demo public community');
  await page.locator('[data-edit="save"]').click();
  await expect.poll(() => state.saved.length).toBe(1);
  expect(state.saved[0].publicPage?.displayName).toBe('Demo public community');
  expect(state.saved[0].games[0].records?.searchPublishing).toBe(true);
  if (!(await publishing.isVisible())) await game.locator(':scope > summary').click();
  await game.getByLabel('Statistics visibility', { exact: true }).selectOption('members');
  await expect(publishing).not.toBeChecked();
  await game.getByLabel('Statistics visibility', { exact: true }).selectOption('public');
  await expect(publishing).not.toBeChecked();
});

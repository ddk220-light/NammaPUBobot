import { expect, test } from '@playwright/test';
import { guild, user, mockApp } from './fixtures';

test('deleting a player requires typing their display name', async ({ page }) => {
  const state = await mockApp(page);
  await page.goto(`/?community=${guild}&view=players`);
  const row = page.locator('[data-rating-row]').first();
  await row.locator('summary').first().click();
  const zone = row.locator('.danger-zone');
  await zone.locator('summary').click();
  await expect(zone).toContainText('This cannot be undone.');
  await expect(zone).toContainText('“Deleted player”');
  const input = zone.getByLabel(`Type ${state.playerName} to confirm`);
  await input.fill('A player');
  await zone.getByRole('button', { name: 'Delete player data' }).click();
  await expect(page.locator('#notice')).toContainText('does not match. Nothing was deleted.');
  expect(state.deletions).toEqual([]);
  await input.fill(state.playerName);
  await zone.getByRole('button', { name: 'Delete player data' }).click();
  await expect(page.locator('#notice')).toContainText('Player data deleted.');
  expect(state.deletions).toEqual([
    { path: `/api/communities/${guild}/player/delete`, body: { userId: user } },
  ]);
  await expect(page.locator('[data-rating-row]')).toHaveCount(0);
});

test('deleting community data requires the server name and returns to first-run setup', async ({
  page,
}) => {
  const state = await mockApp(page);
  const name = 'Friends’ game night — a long community name';
  await page.goto(`/?community=${guild}&view=setup`);
  const block = page.locator('section.danger-zone');
  await expect(block.getByRole('heading', { name: 'Delete community data' })).toBeVisible();
  // The destructive block is the last thing in Setup.
  await expect(page.locator('#view-setup > section:last-child')).toHaveClass(/danger-zone/);
  await expect(block).toContainText('This cannot be undone.');
  const input = block.getByLabel(`Type ${name} to confirm`);
  await input.fill('Friends');
  await block.getByRole('button', { name: 'Delete community data' }).click();
  await expect(page.locator('#notice')).toContainText('Nothing was deleted.');
  expect(state.deletions).toEqual([]);
  await input.fill(name);
  await block.getByRole('button', { name: 'Delete community data' }).click();
  await expect(page.locator('#notice')).toContainText('Community data deleted.');
  expect(state.deletions).toEqual([
    { path: `/api/communities/${guild}/delete`, body: { confirmName: name } },
  ]);
  await expect(page.getByRole('heading', { name: 'Set up your first game' })).toBeVisible();
});

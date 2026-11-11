import { test, expect } from '@playwright/test';
import { mockApp, guild, queue, user } from './fixtures';

test('empty and paused queues explain why start is unavailable; odd manual starts retain their policy', async ({
  page,
}) => {
  const state = await mockApp(page);
  await page.goto(`/app/?community=${guild}&view=queues`);
  const card = page.locator('.queue-card').first();
  await expect(card.getByRole('button', { name: 'Need 2 players to start' })).toBeDisabled();
  await expect(card.getByRole('button', { name: 'Clear queue', exact: true })).toHaveCount(0);
  state.waitingCount = 1;
  await page.reload();
  await expect(card.getByRole('button', { name: 'Need 2 players to start' })).toBeDisabled();
  await expect(card.getByRole('button', { name: 'Clear queue', exact: true })).toBeVisible();
  state.waitingCount = 5;
  await page.reload();
  await expect(card).toContainText('Manual start uses uneven teams');
  await card.getByRole('button', { name: 'Start with 5 now' }).click();
  expect(state.actions).toHaveLength(0);
  await expect(card.locator('div[data-review]')).toContainText('saved rules');
  await card.getByRole('button', { name: 'Confirm action' }).click();
  expect(state.actions[0]).toMatchObject({ kind: 'start', queue });
  state.snapshot.settings.queues[0].enabled = false;
  await page.reload();
  await expect(card.getByRole('button', { name: 'Queue paused' })).toBeDisabled();
});

test('roster repair selects names, escapes search results and requires reviewed intent', async ({
  page,
}) => {
  const state = await mockApp(page);
  state.playerCount = 3;
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route('**/members?*', (route) =>
    route.fulfill({
      json: {
        members: [
          { id: '100000000000000099', name: '<img src=x onerror=alert(1)>', username: 'newplayer' },
        ],
      },
    }),
  );
  await page.goto(`/app/?community=${guild}&view=matches`);
  const admin = page.locator('.match-admin');
  await admin.locator(':scope > summary').click();
  await admin.getByText('Replace a player', { exact: true }).click();
  const replacement = admin
    .locator('form')
    .filter({ has: page.locator('[name="kind"][value="force_sub"]') });
  await replacement.getByLabel('Who is leaving?', { exact: true }).selectOption(user);
  await replacement.getByLabel('Search server members').fill('New');
  await replacement.getByRole('button', { name: 'Search', exact: true }).click();
  await expect(replacement.locator('[data-member-status]')).toContainText('1 matching members');
  await replacement
    .getByLabel('Who replaces them?', { exact: true })
    .selectOption('100000000000000099');
  await expect(replacement.locator('img')).toHaveCount(0);
  await replacement.getByRole('button', { name: 'Review replacement' }).click();
  await expect(replacement.locator('div[data-review]')).toContainText(state.playerName);
  await expect(replacement.locator('div[data-review]')).not.toContainText(user);
  expect(
    await replacement.evaluate((form) => {
      const review = form.querySelector('div[data-review]')!;
      const confirm = form.querySelector('button[data-original-label]')!;
      return Boolean(review.compareDocumentPosition(confirm) & Node.DOCUMENT_POSITION_FOLLOWING);
    }),
  ).toBe(true);
  expect(state.actions).toHaveLength(0);
  await replacement
    .getByLabel('Who replaces them?', { exact: true })
    .selectOption(String(BigInt(user) + 1n));
  await expect(replacement.locator('div[data-review]')).toHaveCount(0);
  await replacement.getByRole('button', { name: 'Review replacement' }).click();
  expect(state.actions).toHaveLength(0);
  await replacement.getByRole('button', { name: 'Confirm action' }).click();
  expect(state.actions[0]).toMatchObject({
    kind: 'force_sub',
    target: user,
    replacement: String(BigInt(user) + 1n),
    version: 1,
  });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('failed and stale member searches preserve the existing player choices', async ({ page }) => {
  await mockApp(page);
  let fail = true;
  let release: () => void = () => {};
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route('**/members?*', async (route) => {
    if (fail)
      return route.fulfill({
        status: 503,
        json: { error: 'Discord unavailable', code: 'discord_unavailable' },
      });
    await gate;
    await route.fulfill({
      json: { members: [{ id: '100000000000000099', name: 'Stale member' }] },
    });
  });
  await page.goto(`/app/?community=${guild}&view=matches`);
  const admin = page.locator('.match-admin');
  await admin.locator(':scope > summary').click();
  await admin.getByText('Place or move a player', { exact: true }).click();
  const form = admin.locator('form').filter({ has: page.locator('[name="kind"][value="put"]') });
  await form.getByLabel('Player to place', { exact: true }).selectOption(user);
  await form.getByLabel('Search server members').fill('Old');
  await form.getByRole('button', { name: 'Search', exact: true }).click();
  await expect(form.locator('[data-member-status]')).toContainText(
    'Loaded choices are still available',
  );
  await expect(form.getByLabel('Player to place', { exact: true })).toHaveValue(user);
  fail = false;
  await form.getByRole('button', { name: 'Search', exact: true }).click();
  await form.getByLabel('Search server members').fill('New query');
  release();
  await expect(form.getByRole('button', { name: 'Search', exact: true })).toBeEnabled();
  await expect(form.locator('option')).not.toContainText(['Stale member']);
  await expect(form.getByLabel('Player to place', { exact: true })).toHaveValue(user);
});

test('public invitations and substantive game pages work without JavaScript at narrow widths', async ({
  browser,
}) => {
  const context = await browser.newContext({
    javaScriptEnabled: false,
    viewport: { width: 360, height: 800 },
  });
  const page = await context.newPage();
  for (const path of ['/', '/games/age-of-empires-2/']) {
    await page.goto(path);
    await expect(
      page.getByRole('link', { name: 'Add to Discord', exact: true }).first(),
    ).toHaveAttribute('href', '/invite');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
  }
  await expect(page.getByRole('heading', { name: 'How a game night goes' })).toBeVisible();
  await expect(page.locator('.article-body')).toContainText('What it can’t do yet');
  await context.close();
});

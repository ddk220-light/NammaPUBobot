import { expect, test, type Page } from '@playwright/test';
import { game, guild, mockApp, otherGuild, queue, user } from './fixtures';

const organizer = `/?community=${guild}&view=queues&queue=${queue}`;
const field = (page: Page, name: string) => page.getByRole('spinbutton', { name, exact: true });
const tab = (page: Page, name: string) => page.getByRole('tab', { name, exact: true });
const save = (page: Page) =>
  page.getByRole('button', { name: 'Save changes', exact: true }).click();
async function openGame(page: Page) {
  await tab(page, 'Setup').click();
  const details = page.locator('.game-settings');
  if (!(await details.evaluate((el) => (el as HTMLDetailsElement).open)))
    await details.locator(':scope > summary').click();
}
async function seedPreview(page: Page) {
  await tab(page, 'Players').click();
  await page.locator('[data-import-section] > summary').click();
  const form = page.locator('[data-kind="import-preview"]');
  await form
    .locator('[name=content]')
    .fill(
      `id,name,rating,deviation,wins,losses,draws,streak,hidden,lastRankedAt\n${user},Sam,1400,120,12,8,2,-3,true,2026-10-20T10:30:00Z`,
    );
  await form.getByRole('button', { name: 'Preview import' }).click();
  return form;
}

test('reverting a shared rating and tax edit after adding a queue keeps every queue consistent', async ({
  page,
}) => {
  const state = await mockApp(page);
  state.snapshot.settings.queues[0].modules.gold = true;
  state.snapshot.settings.queues[0].economy!.taxEnabled = true;
  await page.goto(organizer);
  await openGame(page);
  await field(page, 'Starting rating').fill('1234');
  await field(page, 'Thursday tax time (UTC minutes)').fill('780');
  await tab(page, 'Queues').click();
  await page.getByRole('button', { name: 'Add queue', exact: true }).click();
  await openGame(page);
  await field(page, 'Starting rating').fill('1000');
  await field(page, 'Thursday tax time (UTC minutes)').fill('600');
  await save(page);
  await expect(page.locator('#notice')).toContainText('Settings saved');
  expect(state.saved[0].queues.map((q) => q.ratingSettings!.initial)).toEqual([1000, 1000]);
  expect(state.saved[0].queues.map((q) => q.economy)).toEqual([
    { taxEnabled: true, taxMinuteUtc: 600 },
    { taxEnabled: false, taxMinuteUtc: 600 },
  ]);
});

test('a temporarily invalid game policy does not become a new queue default', async ({ page }) => {
  const state = await mockApp(page);
  await page.goto(organizer);
  await openGame(page);
  await field(page, 'Starting rating').fill('');
  await tab(page, 'Queues').click();
  await page.getByRole('button', { name: 'Add queue', exact: true }).click();
  await openGame(page);
  await expect(field(page, 'Starting rating')).toHaveValue('');
  await field(page, 'Starting rating').fill('1000');
  await save(page);
  await expect(page.locator('#notice')).toContainText('Settings saved');
  expect(state.saved[0].queues.map((q) => q.ratingSettings!.initial)).toEqual([1000, 1000]);
});

test('a newly locked rating system can be restored without losing other draft fields', async ({
  page,
}) => {
  const state = await mockApp(page);
  await page.goto(organizer);
  await page.getByRole('textbox', { name: 'Queue name', exact: true }).fill('Keep this edit');
  await openGame(page);
  await page.getByRole('combobox', { name: 'Rating system', exact: true }).selectOption('flat');
  await field(page, 'Starting rating').fill('1234');
  state.locked = true;
  await page.getByRole('button', { name: 'Account', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(page.getByRole('combobox', { name: 'Rating system', exact: true })).toBeDisabled();
  await save(page);
  expect(state.saved).toHaveLength(0);
  await page.getByRole('button', { name: 'Use saved rating system' }).click();
  await expect(field(page, 'Starting rating')).toHaveValue('1234');
  await save(page);
  await expect(page.locator('#notice')).toContainText('Settings saved');
  expect(state.saved[0].queues[0]).toMatchObject({
    ratingEngine: 'aoe2',
    name: 'Keep this edit',
    ratingSettings: { initial: 1234 },
  });
});

test('recreating the first queue inherits a stored engine even with no saved queues', async ({
  page,
}) => {
  const state = await mockApp(page);
  state.snapshot.settings.queues = [];
  state.storedEngine = 'glicko2';
  state.locked = true;
  await page.goto(organizer);
  await page.getByRole('button', { name: 'Add queue', exact: true }).click();
  await openGame(page);
  await expect(page.getByRole('combobox', { name: 'Rating system', exact: true })).toHaveValue(
    'glicko2',
  );
  await save(page);
  await expect(page.locator('#notice')).toContainText('Settings saved');
  expect(state.saved[0].queues[0].ratingEngine).toBe('glicko2');
});

test('conflicting saved settings update readiness while keeping the draft base revision', async ({
  page,
}) => {
  const state = await mockApp(page);
  await page.goto(organizer);
  await page.getByRole('textbox', { name: 'Queue name', exact: true }).fill('Local draft');
  state.snapshot.settings.queues[0].enabled = false;
  state.snapshot.revision++;
  await tab(page, 'Setup').click();
  await page.getByRole('button', { name: 'Check access again', exact: true }).click();
  await expect(page.locator('#view-setup')).not.toContainText('Ready to play');
  await expect(page.getByRole('button', { name: 'Reload latest' })).toBeVisible();
  await tab(page, 'Queues').click();
  await page.locator('[data-queue-list]').click();
  await expect(page.locator('.queue-card').first().locator('.status-pill')).toHaveText('Paused');
  await page.getByRole('button', { name: 'Settings for Local draft', exact: true }).click();
  await expect(page.getByRole('textbox', { name: 'Queue name', exact: true })).toHaveValue(
    'Local draft',
  );
  await save(page);
  await expect(page.locator('#notice')).toContainText('Settings changed');
  expect(state.saveRevisions).toEqual([3]);
  expect(state.snapshot.revision).toBe(4);
  await expect(page.locator('#savebar')).toBeVisible();
});

test('community history preserves the destination and cancelling Back preserves both entries', async ({
  page,
}) => {
  await mockApp(page);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(organizer);
  await page.locator('#community').selectOption(otherGuild);
  await expect(page.locator('#view-queues')).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`community=${otherGuild}.*view=queues`));
  await page.getByRole('button', { name: 'Settings for Evening games', exact: true }).click();
  await page
    .getByRole('textbox', { name: 'Queue name', exact: true })
    .fill('Other community draft');
  // First Back returns to the queue list in the same community and keeps the draft.
  await page.goBack();
  await expect(page.locator('.queue-card')).toContainText('Other community draft');
  await expect(page.locator('#savebar')).toBeVisible();
  page.once('dialog', (dialog) => dialog.dismiss());
  await page.goBack();
  await expect(page).toHaveURL(new RegExp(otherGuild));
  await expect(page.locator('.queue-card')).toContainText('Other community draft');
  page.once('dialog', (dialog) => dialog.accept());
  await page.goBack();
  await expect(page).toHaveURL(new RegExp(`community=${guild}.*view=queues`));
  await expect(page.locator('#community')).toHaveValue(guild);
  await page.goForward();
  await expect(page.locator('#community')).toHaveValue(otherGuild);
  await expect(page.locator('#view-queues')).toBeVisible();
});

test('Back closes the game panel and restores persistent validation errors', async ({ page }) => {
  await mockApp(page);
  await page.goto(organizer);
  await field(page, 'Players').fill('');
  await save(page);
  await expect(field(page, 'Players')).toHaveAttribute('aria-invalid', 'true');
  await page.getByRole('button', { name: 'Maps', exact: true }).click();
  await page.goBack();
  await expect(field(page, 'Players')).toBeVisible();
  await expect(field(page, 'Players')).toHaveValue('');
  await expect(field(page, 'Players')).toHaveAttribute('aria-invalid', 'true');
  await expect(page.locator('#notice')).toBeVisible();
  await openGame(page);
  await page.goBack();
  await expect(page.locator('.game-settings')).not.toHaveAttribute('open', '');
  await page.goForward();
  await expect(page.locator('.game-settings')).toHaveAttribute('open', '');
  await tab(page, 'Queues').click();
  await expect(field(page, 'Players')).toHaveAttribute('aria-invalid', 'true');
  await field(page, 'Players').fill('8');
  await expect(field(page, 'Players')).not.toHaveAttribute('aria-invalid');
});

test('cross-field errors point to the game field and persist through other screens', async ({
  page,
}) => {
  await mockApp(page);
  await page.goto(organizer);
  await openGame(page);
  await field(page, 'Initial deviation').fill('100');
  await field(page, 'Minimum deviation').fill('200');
  await save(page);
  await expect(field(page, 'Minimum deviation')).toHaveAttribute('aria-invalid', 'true');
  await expect(page.locator('.field-error')).toContainText(
    'Minimum deviation cannot exceed initial deviation.',
  );
  await tab(page, 'Queues').click();
  await page.getByRole('button', { name: 'Account', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Refresh', exact: true }).click();
  await openGame(page);
  await expect(field(page, 'Minimum deviation')).toHaveAttribute('aria-invalid', 'true');
  await field(page, 'Minimum deviation').fill('50');
  await save(page);
  await expect(page.locator('#notice')).toContainText('Settings saved');
});

test('server role errors mark nested queue fields and every affected rank row', async ({
  page,
}) => {
  await mockApp(page);
  await page.goto(organizer);
  await page.getByRole('textbox', { name: 'Queue name', exact: true }).fill('Dirty');
  await page.route('**/config', (route) =>
    route.fulfill({
      status: 400,
      json: {
        code: 'wrong_role',
        error: 'Choose available roles.',
        requestId: 'role-review',
        issues: [
          {
            path: ['settings', 'queues', 0, 'controls', 'requiredRole'],
            message: 'Required role is unavailable.',
          },
          {
            path: ['settings', 'games', 0, 'records', 'ranks', 0, 'roleId'],
            message: 'Rank role is unavailable.',
          },
          {
            path: ['settings', 'games', 0, 'records', 'ranks', 1, 'roleId'],
            message: 'Rank role is unavailable.',
          },
        ],
      },
    }),
  );
  await save(page);
  await expect(page.locator('[name=requiredRole]')).toBeVisible();
  await expect(page.locator('[name=requiredRole]')).toHaveAttribute('aria-invalid', 'true');
  await expect(page.locator('#notice')).toContainText('role-review');
  await openGame(page);
  await expect(page.locator('[name=rank-0-role]')).toHaveAttribute('aria-invalid', 'true');
  await expect(page.locator('[name=rank-1-role]')).toHaveAttribute('aria-invalid', 'true');
});

test('drawer navigation restores focus after rendering and touch links have full hit areas', async ({
  page,
}) => {
  const state = await mockApp(page);
  state.playerName = 'Sam';
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(organizer);
  const menu = page.getByRole('button', { name: 'Choose community or queue' });
  await menu.click();
  await page.locator(`[data-drawer-queue="${queue}"]`).click();
  await expect(menu).toBeFocused();
  await menu.click();
  await page.getByRole('button', { name: 'Another community', exact: true }).click();
  await expect(page.locator('#view-queues')).toBeVisible();
  await expect(menu).toBeFocused();
  await tab(page, 'Players').click();
  await page.locator('.player-record > summary').click();
  const name = page.getByRole('link', { name: 'Open player profile', exact: true });
  const r = await name.boundingBox();
  expect(r!.width).toBeGreaterThanOrEqual(44);
  expect(r!.height).toBeGreaterThanOrEqual(44);
  await name.click();
  const link = page.getByRole('link', { name: 'Organizer setup' });
  const box = await link.boundingBox();
  expect(box!.width).toBeGreaterThanOrEqual(44);
  expect(box!.height).toBeGreaterThanOrEqual(44);
});

test('a committed import stays successful when record refresh fails and preserves a settings draft', async ({
  page,
}, testInfo) => {
  const state = await mockApp(page);
  await page.goto(organizer);
  await page.getByRole('textbox', { name: 'Queue name', exact: true }).fill('Keep after import');
  await seedPreview(page);
  await page.route('**/records', (route) =>
    route.fulfill({
      status: 503,
      json: { error: 'Temporarily unavailable', code: 'unavailable', requestId: 'refresh-failed' },
    }),
  );
  await page.getByRole('button', { name: 'Apply reviewed import' }).click();
  await expect(page.getByRole('heading', { name: 'Import applied', exact: true })).toBeVisible();
  await expect(page.locator('#import-result')).toContainText('refresh-failed');
  await expect(page.locator('#import-result')).toContainText('Players in source');
  await expect(page.locator('#import-result dd').first()).toHaveText('1');
  await expect(page.getByRole('button', { name: 'Apply reviewed import' })).toHaveCount(0);
  await expect(page.getByText('Nothing has been imported yet.', { exact: false })).toHaveCount(0);
  expect(state.importCalls).toBe(1);
  await page.screenshot({ path: testInfo.outputPath('import-applied-refresh-failed.png') });
  await tab(page, 'Queues').click();
  await expect(page.getByRole('textbox', { name: 'Queue name', exact: true })).toHaveValue(
    'Keep after import',
  );
  await tab(page, 'Players').click();
  await page.unroute('**/records');
  await page.getByRole('button', { name: 'Retry record refresh' }).click();
  await expect(page.getByRole('heading', { name: 'Import applied', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Retry record refresh' })).toHaveCount(0);
  expect(state.importCalls).toBe(1);
});

test('import review includes all player values and paged match and rating history', async ({
  page,
}, testInfo) => {
  await mockApp(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(organizer);
  const form = await seedPreview(page);
  await page.getByText('Review 1 player records', { exact: true }).click();
  const card = page.locator('.import-player-card');
  for (const label of [
    'Discord ID',
    'Source ladder',
    'Destination game',
    'Rating',
    'Deviation',
    'Wins',
    'Losses',
    'Draws',
    'Streak',
    'Hidden from statistics',
    'Last ranked match',
  ])
    await expect(card.getByText(label, { exact: true })).toBeVisible();
  await expect(card).toContainText('2026-10-20T10:30:00.000Z');
  await expect(card).toContainText('-3');
  await card.scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath('import-player-values.png') });
  const bundle = {
    version: 1,
    source: 'nammapubobot',
    sourceId: 'history-review',
    mode: 'history',
    unsupported: [],
    mappings: [{ ladder: 'ladder', gameId: game, engine: 'aoe2', queues: { old: queue } }],
    players: [],
    matches: Array.from({ length: 51 }, (_, i) => ({
      sourceId: `match-${i}`,
      ladder: 'ladder',
      queue: 'old',
      at: 1792492200000,
      endedAt: 1792495800000,
      result: 'alpha',
      teams: [[user], ['100000000000000003']],
    })),
    changes: Array.from({ length: 51 }, (_, i) => ({
      sourceId: `change-${i}`,
      ladder: 'ladder',
      userId: user,
      matchId: `match-${i}`,
      at: 1792495800000,
      before: 1400,
      after: 1420,
      deviationBefore: 120,
      deviationAfter: 118,
      reason: `Imported result ${i}`,
    })),
  };
  await form.locator('[name=format]').selectOption('json');
  await form.locator('[name=content]').fill(JSON.stringify(bundle));
  await form.getByRole('button', { name: 'Preview import' }).click();
  for (const kind of ['matches', 'changes']) {
    const details = page.locator(`[data-import-history="${kind}"]`);
    await details.locator('summary').click();
    await expect(details.locator('.import-history-card')).toHaveCount(50);
    await details.getByRole('button', { name: /^Next/ }).click();
    await expect(details.locator('.import-history-card')).toHaveCount(1);
    await expect(details.locator('[data-import-range]')).toContainText('Showing 51–51 of 51');
    await expect(details.locator('.import-history-card').last()).toContainText(
      kind === 'matches' ? 'Destination queue' : 'Deviation after',
    );
    await expect(details.locator('.import-history-card').last()).toContainText(
      kind === 'matches' ? 'Evening games' : 'Imported result 50',
    );
    await details.getByRole('button', { name: /^Previous/ }).click();
    await expect(details.locator('.import-history-card')).toHaveCount(50);
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('statistics failures keep request IDs and authentication recovery', async ({ page }) => {
  await mockApp(page);
  await page.route('**/statistics/**', (route) =>
    route.fulfill({
      status: 403,
      json: { error: 'Membership required', code: 'members_only', requestId: 'stats-denied' },
    }),
  );
  await page.goto(`/stats?community=${guild}&game=${game}`);
  await expect(page.getByRole('alert')).toContainText('stats-denied');
  await expect(page.getByRole('link', { name: 'Sign in with Discord' })).toBeVisible();
  await page.route('**/statistics/**', (route) =>
    route.fulfill({
      status: 503,
      headers: { 'X-Request-ID': 'stats-upstream' },
      body: 'Unavailable',
    }),
  );
  await page.getByRole('button', { name: 'Retry' }).click();
  await expect(page.getByRole('alert')).toContainText('stats-upstream');
  await expect(page.getByRole('link', { name: 'Sign in with Discord' })).toHaveCount(0);
});

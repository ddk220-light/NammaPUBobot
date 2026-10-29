import { expect, test, type Page } from '@playwright/test';
import { mockApp, guild, game, queue, user } from './fixtures';

const tab = (page: Page, name: string) => page.getByRole('tab', { name, exact: true });
async function freshGuide(page: Page) {
  const state = await mockApp(page);
  state.snapshot.settings = { games: [], queues: [] };
  await page.goto(`/?community=${guild}&view=setup`);
  await page.getByRole('button', { name: 'Start guided setup', exact: true }).click();
  await page.getByRole('button', { name: 'Choose game', exact: true }).click();
  await page.getByLabel('Game for guided setup').selectOption('custom');
  await page.getByLabel('Game for guided setup').selectOption('catalog:aoe2');
  await page.getByRole('button', { name: 'Create queue draft' }).click();
  return state;
}

test('guided first queue keeps invalid input and saves one atomic community draft', async ({
  page,
}, info) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const state = await freshGuide(page);
  const form = page.locator('[data-guide-queue]');
  await form.getByLabel('Queue name', { exact: true }).fill('Friday night');
  await form.getByLabel('Players', { exact: true }).fill('');
  await tab(page, 'Players').click();
  await page.setViewportSize({ width: 844, height: 390 });
  await tab(page, 'Setup').click();
  await expect(form.getByLabel('Players', { exact: true })).toHaveValue('');
  await form.getByLabel('Players', { exact: true }).fill('6');
  await form.getByLabel('Team formation').selectOption('balanced');
  await expect(page.locator('.discord-preview')).toContainText('3 vs 3');
  await expect(page.locator('#view-setup')).not.toContainText('Ready to play');
  expect(state.saved).toHaveLength(0);
  await page.getByRole('button', { name: 'Review and save' }).click();
  await expect(page.getByRole('dialog')).toContainText('Friday night');
  await page.getByRole('button', { name: 'Save reviewed changes' }).click();
  await expect(page.locator('#savebar')).toBeHidden();
  expect(state.saved).toHaveLength(1);
  expect(state.saved[0].games).toHaveLength(1);
  expect(state.saved[0].queues[0]).toMatchObject({
    name: 'Friday night',
    size: 6,
    teamMethod: 'balanced',
    enabled: true,
    checkIn: { enabled: true, seconds: 60 },
  });
  expect(state.saveRevisions).toEqual([3]);
  await expect(page).toHaveURL(/guide=try/);
  await expect(page.getByRole('heading', { name: 'Try your queue in Discord' })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: info.outputPath('guided-setup-complete.png') });
});

test('guide survives failed save and return from Discord without falsely completing', async ({
  page,
}, info) => {
  const state = await freshGuide(page);
  await page.locator('[data-guide-queue] [name="name"]').fill('Keep this queue');
  state.failSave = 'revision_conflict';
  await page.getByRole('button', { name: 'Review and save' }).click();
  await page.getByRole('button', { name: 'Save reviewed changes' }).click();
  await expect(page.locator('#savebar')).toBeVisible();
  await expect(page.locator('[data-guide-queue] [name="name"]')).toHaveValue('Keep this queue');
  state.access = 'unavailable';
  await page.getByRole('button', { name: 'Back', exact: true }).click();
  await page.getByRole('button', { name: 'Back', exact: true }).click();
  await page.locator('.guided-setup [data-discord-invite]').evaluate((el) => {
    el.addEventListener('click', (e) => e.preventDefault(), { once: true });
    (el as HTMLElement).click();
  });
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect(page.locator('#discord-access')).toContainText('Not verified');
  await expect(page.locator('#view-setup')).not.toContainText('Ready to play');
  await expect(page.locator('#savebar')).toBeVisible();
  await page.getByRole('button', { name: 'Choose game', exact: true }).click();
  await page.getByRole('button', { name: 'Create queue draft' }).click();
  await expect(page.locator('[data-guide-queue] [name="name"]')).toHaveValue('Keep this queue');
  expect(state.saved).toHaveLength(1);
  await page.screenshot({ path: info.outputPath('guided-setup-draft.png') });
});

test('permission recovery requests only the missing configured feature', async ({ page }, info) => {
  const state = await mockApp(page);
  state.snapshot.settings.games[0].records!.decorateNickname = true;
  state.manageRoles = false;
  await page.goto(`/?community=${guild}&view=setup`);
  const recovery = page.locator('#discord-access');
  await expect(page.locator('#view-setup')).not.toContainText('Ready to play');
  await expect(recovery.getByRole('link', { name: 'Grant Manage Nicknames' })).toHaveAttribute(
    'href',
    /permissions=134302720/,
  );
  await expect(recovery.getByRole('link', { name: 'Grant Manage Roles' })).toHaveCount(0);
  await page.locator('.game-settings > summary').click();
  await page.getByRole('checkbox', { name: 'Show rating in nicknames', exact: true }).uncheck();
  await expect(recovery.getByRole('link', { name: 'Grant Manage Nicknames' })).toHaveCount(0);
  await page.getByRole('checkbox', { name: 'Show rating in nicknames', exact: true }).check();
  await expect(recovery.getByRole('link', { name: 'Grant Manage Nicknames' })).toBeVisible();
  await recovery.getByText('How to fix access in Discord').click();
  await expect(recovery).toContainText(
    'Inviting again cannot repair role order or channel overrides',
  );
  await page.screenshot({ path: info.outputPath('permission-recovery.png') });
});

test('queue cards stage pause and use reviewed actions with saved rules', async ({
  page,
}, info) => {
  const state = await mockApp(page);
  await page.goto(`/?community=${guild}&view=queues`);
  const card = page.locator('.queue-card').first();
  await card.getByRole('button', { name: 'Pause on save' }).click();
  await expect(card.locator('.status-pill')).toHaveText('Open');
  await expect(card).toContainText('pause on save');
  expect(state.saved).toHaveLength(0);
  await card.locator('[name="kind"]').selectOption('clear');
  await card.getByRole('button', { name: 'Review queue action' }).click();
  expect(state.actions).toHaveLength(0);
  await expect(card).toContainText('Clear all waiting players');
  await card.getByRole('button', { name: 'Confirm action' }).click();
  expect(state.actions[0]).toMatchObject({ kind: 'clear', queue });
  await expect(page.locator('#savebar')).toBeVisible();
  await page.locator('[data-edit="save"]').click();
  await expect(card.locator('.status-pill')).toHaveText('Paused');
  await page.screenshot({ path: info.outputPath('queue-cards.png') });
});

test('player cards preserve scoped actions, confirmation and authoritative visibility', async ({
  page,
}, info) => {
  const state = await mockApp(page);
  await page.goto(`/?community=${guild}&view=players`);
  const card = page.locator('.player-record').first();
  await card.locator('summary').first().click();
  await expect(card).toContainText('Deviation / uncertainty');
  await card.getByRole('button', { name: 'Hide from statistics', exact: true }).click();
  expect(state.playerActions).toHaveLength(0);
  await card.getByRole('button', { name: 'Hide from statistics', exact: true }).click();
  expect(state.playerActions[0]).toMatchObject({ gameId: game, userId: user, kind: 'hide' });
  await expect(card.locator('.status-pill')).toHaveText('Hidden');
  await card.locator('summary').first().click();
  await card.locator('.rating-options > summary').click();
  await card.getByRole('spinbutton', { name: /Rating for/ }).fill('1500');
  await card.getByRole('button', { name: 'Review rating change' }).click();
  expect(state.actions).toHaveLength(0);
  await card.getByRole('button', { name: 'Confirm action' }).click();
  expect(state.actions[0]).toMatchObject({ kind: 'seed', target: user, queue, rating: 1500 });
  await card.locator('summary').first().click();
  await page.screenshot({ path: info.outputPath('player-management.png') });
});

test('matches show actual readiness and settings help includes defaults and duration', async ({
  page,
}, info) => {
  const state = await mockApp(page);
  state.matchState = 'check_in';
  await page.goto(`/?community=${guild}&view=matches`);
  await expect(page.getByRole('progressbar', { name: 'Players checked in' })).toHaveAttribute(
    'value',
    '1',
  );
  await expect(page.locator('.match-record')).toContainText('1 / 1 checked in');
  await expect(page.locator('.match-record')).toContainText('No rating changes recorded');
  await page.screenshot({ path: info.outputPath('match-check-in.png') });
  await tab(page, 'Queues').click();
  await page.getByRole('button', { name: 'Match Preparation', exact: true }).click();
  const input = page.getByRole('spinbutton', {
    name: 'Check-in time limit (seconds)',
    exact: true,
  });
  await input.fill('90');
  await expect(input.locator('..')).toContainText('1 min 30 s');
  await page.getByRole('button', { name: 'More about Check-in time limit (seconds)' }).click();
  await expect(page.getByRole('dialog')).toContainText('Default');
  await expect(page.getByRole('dialog')).toContainText('60');
  await expect(page.getByRole('dialog')).toContainText('Applies to');
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: 'Review changes', exact: true }).click();
  await expect(page.getByRole('dialog')).toContainText('90');
  await expect(page.getByRole('dialog')).toContainText('1 changed values');
  await page.screenshot({ path: info.outputPath('settings-change-review.png') });
});

test('custom game and advanced queue edits stay synchronized with the guide', async ({ page }) => {
  const state = await mockApp(page);
  state.snapshot.settings = { games: [], queues: [] };
  await page.goto(`/?community=${guild}&view=setup`);
  await page.getByRole('button', { name: 'Start guided setup', exact: true }).click();
  await page.getByRole('button', { name: 'Choose game', exact: true }).click();
  await page.getByLabel('Game for guided setup').selectOption('custom');
  await page.locator('[data-guide-name]').fill('Friday board games');
  await tab(page, 'Overview').click();
  await tab(page, 'Setup').click();
  await expect(page.locator('[data-guide-name]')).toHaveValue('Friday board games');
  await page.getByRole('button', { name: 'Create queue draft' }).click();
  const form = page.locator('[data-guide-queue]');
  await form.getByLabel('Players', { exact: true }).fill('17');
  await page.getByRole('button', { name: 'Review and save' }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(state.saved).toHaveLength(0);
  await form.getByLabel('Players', { exact: true }).fill('4');
  await page.getByRole('button', { name: 'Advanced queue settings' }).click();
  await page.getByRole('button', { name: 'Match Preparation', exact: true }).click();
  await page.getByRole('checkbox', { name: 'Require check-in', exact: true }).uncheck();
  await page.getByRole('button', { name: 'Add queue', exact: true }).click();
  await tab(page, 'Setup').click();
  await form.getByLabel('Queue name', { exact: true }).fill('First custom queue');
  await page.getByRole('button', { name: 'Refresh records' }).click();
  await expect(page.locator('.discord-preview')).toContainText('Check-in off');
  await expect(form.getByLabel('Queue name', { exact: true })).toHaveValue('First custom queue');
  await page.getByRole('button', { name: 'Review and save' }).click();
  await page.getByRole('button', { name: 'Save reviewed changes' }).click();
  expect(state.saved[0].games[0].name).toBe('Friday board games');
  expect(state.saved[0].queues[0]).toMatchObject({
    name: 'First custom queue',
    size: 4,
    checkIn: { enabled: false },
  });
  expect(state.saved[0].queues).toHaveLength(2);
});

test('expanded player cards paginate and search every loaded player by Discord ID', async ({
  page,
}) => {
  const state = await mockApp(page);
  state.playerCount = 51;
  await page.goto(`/?community=${guild}&view=players`);
  await expect(page.locator('[data-rating-row]')).toHaveCount(50);
  const last = String(BigInt(user) + 50n);
  await page.getByRole('searchbox', { name: 'Find a player' }).fill(last);
  await expect(page.locator('[data-filter-empty="rating"]')).toContainText('loaded records');
  await page.getByRole('button', { name: 'Load more players' }).click();
  await expect(page.locator('[data-rating-row]:visible')).toHaveCount(1);
  await expect(page.locator('[data-rating-row]:visible')).toContainText('Player 51');
  await page.locator('[data-rating-row]:visible > summary').click();
  await expect(page.getByRole('link', { name: 'Open player profile' })).toHaveAttribute(
    'href',
    new RegExp(`user=${last}`),
  );
  await expect(page.getByRole('button', { name: 'Load more players' })).toBeHidden();
});

test('community discovery recovery remains reachable without a configured community', async ({
  page,
}) => {
  const state = await mockApp(page);
  state.noCommunities = true;
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'No communities yet' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Add to Discord' })).toBeVisible();
  state.noCommunities = false;
  await page.getByRole('button', { name: 'Refresh communities' }).click();
  await expect(page.getByRole('heading', { name: 'Overview', exact: true })).toBeVisible();
});

for (const [width, height] of [
  [360, 780],
  [768, 1024],
  [844, 390],
]) {
  test(`guided fields and actions remain reachable at ${width}×${height}`, async ({
    page,
  }, info) => {
    await page.setViewportSize({ width, height });
    const state = await freshGuide(page);
    state.failSave = 'revision_conflict';
    await page.getByRole('button', { name: 'Review and save' }).click();
    await page.getByRole('button', { name: 'Save reviewed changes' }).click();
    const field = page.locator('[data-guide-queue] [name="teamMethod"]');
    await field.scrollIntoViewIfNeeded();
    await expect(field).toBeInViewport();
    await expect(page.locator('[data-edit="save"]')).toBeInViewport();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
    const size = await field.evaluate((el) => getComputedStyle(el).fontSize);
    expect(parseFloat(size)).toBeGreaterThanOrEqual(16);
    const targets = await page
      .locator('.guided-setup button:visible, #savebar button:visible')
      .evaluateAll((els) =>
        els.map((el) => ({
          w: el.getBoundingClientRect().width,
          h: el.getBoundingClientRect().height,
        })),
      );
    expect(targets.every((r) => r.w >= 44 && r.h >= 44)).toBe(true);
    await page.screenshot({ path: info.outputPath(`guide-${width}.png`) });
  });
}

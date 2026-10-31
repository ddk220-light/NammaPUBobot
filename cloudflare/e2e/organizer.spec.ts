import { expect, test } from '@playwright/test';
import { mockApp, guild, otherGuild, game, queue, user } from './fixtures';

const organizer = `/?community=${guild}&view=queues`;
test('signing out keeps edits on cancellation or failure and clears them only after success', async ({
  page,
}) => {
  await mockApp(page);
  await page.goto(organizer);
  const name = page.getByRole('textbox', { name: 'Queue name', exact: true });
  await name.fill('Before sign out');
  const logout = {
    click: async () => {
      await page.getByRole('button', { name: 'Account', exact: true }).click();
      await page.getByRole('dialog').getByRole('button', { name: 'Sign out', exact: true }).click();
    },
  };
  page.once('dialog', (dialog) => dialog.dismiss());
  await logout.click();
  await expect(name).toHaveValue('Before sign out');
  await page.route('**/api/logout', (route) => route.abort('failed'));
  page.once('dialog', (dialog) => dialog.accept());
  await logout.click();
  await expect(page.locator('#notice')).toHaveClass(/error/);
  await expect(name).toHaveValue('Before sign out');
  await page.unroute('**/api/logout');
  page.once('dialog', (dialog) => dialog.accept());
  await logout.click();
  await expect(page.getByRole('link', { name: 'Sign in with Discord', exact: true })).toBeVisible();
});

test('draft survives navigation, refresh, validation and a conflict; save is atomic', async ({
  page,
}) => {
  const state = await mockApp(page);
  state.snapshot.settings.queues.push({
    ...structuredClone(state.snapshot.settings.queues[0]),
    id: '81da571b-6a4a-4e5d-8e32-f4050df35a2e',
    name: 'Weekend games',
    isDefault: false,
  });
  await page.goto(organizer);
  await page.getByRole('textbox', { name: 'Queue name', exact: true }).fill('Changed queue');
  await page.getByRole('spinbutton', { name: 'Players', exact: true }).fill('');
  await page.getByRole('tab', { name: 'Players', exact: true }).click();
  await page.getByRole('button', { name: 'Account', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Refresh', exact: true }).click();
  await page.getByRole('tab', { name: 'Queues', exact: true }).click();
  await expect(page.getByRole('textbox', { name: 'Queue name', exact: true })).toHaveValue(
    'Changed queue',
  );
  await expect(page.getByRole('spinbutton', { name: 'Players', exact: true })).toHaveValue('');
  await page.getByRole('button', { name: 'Save changes', exact: true }).click();
  expect(state.saved).toHaveLength(0);
  await page.getByRole('spinbutton', { name: 'Players', exact: true }).fill('10');
  await page.getByRole('tab', { name: 'Setup', exact: true }).click();
  await page.locator('.game-settings > summary').click();
  await page.getByRole('spinbutton', { name: 'Starting rating', exact: true }).fill('1200');
  state.failSave = 'revision_conflict';
  await page.getByRole('button', { name: 'Save changes', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Reload latest' })).toBeVisible();
  expect(state.saved[0].queues.map((q) => q.ratingSettings?.initial)).toEqual([1200, 1200]);
  expect(state.saved[0].queues[0].name).toBe('Changed queue');
  state.failSave = '';
  let release!: () => void;
  state.saveWait = new Promise<void>((resolve) => (release = resolve));
  await page.getByRole('button', { name: 'Save changes', exact: true }).click();
  await expect(
    page.getByRole('spinbutton', { name: 'Starting rating', exact: true }),
  ).toBeDisabled();
  release();
  await expect(page.locator('#savebar')).toBeHidden();
  await expect(page.locator('#notice')).toContainText('Settings saved');
});

test('drawer and help sheet contain focus, close with Escape and preserve community drafts', async ({
  page,
}) => {
  await mockApp(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(organizer);
  await page.getByRole('textbox', { name: 'Queue name', exact: true }).fill('Not discarded');
  const menu = page.getByRole('button', { name: 'Choose community or queue' });
  await menu.click();
  await expect(page.getByRole('dialog')).toBeVisible();
  page.once('dialog', (d) => d.dismiss());
  await page.getByRole('button', { name: 'Another community', exact: true }).click();
  await expect(page).toHaveURL(new RegExp(guild));
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(menu).toBeFocused();
  const help = page.getByRole('button', { name: 'More about Queue name', exact: true });
  await help.click();
  await expect(page.getByRole('dialog')).toContainText('The name players choose');
  await page.keyboard.press('Escape');
  await expect(help).toBeFocused();
  await expect(page.getByRole('textbox', { name: 'Queue name', exact: true })).toHaveValue(
    'Not discarded',
  );
  await menu.click();
  page.once('dialog', (d) => d.accept());
  await page.getByRole('button', { name: 'Another community', exact: true }).click();
  await expect(page).toHaveURL(new RegExp(otherGuild));
  await expect(page.locator('#savebar')).toBeHidden();
});

test('setup is honest about missing access and ratings locks', async ({ page }) => {
  const state = await mockApp(page);
  state.access = 'needs_access';
  state.locked = true;
  await page.goto(`/?community=${guild}&view=setup`);
  await expect(page.getByRole('heading', { name: 'Ready to play' })).toHaveCount(0);
  await expect(page.getByRole('link', { name: 'Add to Discord', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'I’m back — check access' })).toBeVisible();
  await page.locator('.game-settings > summary').click();
  await expect(page.getByRole('combobox', { name: 'Rating system', exact: true })).toBeDisabled();
  await expect(page.getByText('Locked because this game has player ratings.')).toBeVisible();
});

test('import previews cannot be reused after inputs change and errors prevent apply', async ({
  page,
}) => {
  const state = await mockApp(page);
  await page.goto(`/?community=${guild}&view=players`);
  await page.locator('[data-import-section] > summary').click();
  const form = page.locator('[data-kind="import-preview"]');
  await form.locator('[name="content"]').fill(`id,name,rating,deviation\n${user},Player,1400,120`);
  await form.getByRole('button', { name: 'Preview import' }).click();
  await expect(page.getByRole('button', { name: 'Apply reviewed import' })).toBeVisible();
  await form.locator('[name="sourceId"]').fill('changed-source');
  await expect(page.getByRole('button', { name: 'Apply reviewed import' })).toHaveCount(0);
  state.importOk = false;
  await form.getByRole('button', { name: 'Preview import' }).click();
  await expect(page.getByRole('heading', { name: 'Import needs changes' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Apply reviewed import' })).toHaveCount(0);
  state.importOk = true;
  await form.getByRole('button', { name: 'Preview import' }).click();
  await page.getByRole('button', { name: 'Apply reviewed import' }).click();
  await expect(page.locator('#notice')).toContainText('Import applied');
  expect(state.importCalls).toBe(1);
});

test('public profiles have deep links, recent charts, back navigation and access states', async ({
  page,
}) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const state = await mockApp(page);
  await page.goto(`/stats?community=${guild}&game=${game}`);
  await page.locator('[data-user]').first().click();
  await expect(page).toHaveURL(new RegExp(`user=${user}`));
  await page.getByRole('button', { name: 'Rating history', exact: true }).click();
  await expect(page.locator('.rating-chart')).toBeVisible();
  await expect(page).toHaveURL(/section=history/);
  await page.goBack();
  await expect(page.locator('[data-profile-panel="overview"]')).toBeVisible();
  await expect(page.locator('.workspace-nav')).toHaveCount(0);
  state.hiddenStats = true;
  await page.getByRole('button', { name: 'Reload statistics' }).click();
  await expect(page.getByRole('link', { name: 'Sign in with Discord' })).toBeVisible();
  expect(errors).toEqual([]);
});

test('network failures preserve edits and explicit conflict reload requires confirmation', async ({
  page,
}) => {
  await mockApp(page);
  await page.goto(organizer);
  const name = page.getByRole('textbox', { name: 'Queue name', exact: true });
  await name.fill('Keep through failure');
  await page.route('**/config', (route) => route.abort('failed'));
  await page.getByRole('button', { name: 'Save changes', exact: true }).click();
  await expect(page.locator('#notice')).toHaveClass(/error/);
  await expect(name).toHaveValue('Keep through failure');
  await expect(page.locator('#savebar')).toBeVisible();
  await page.route('**/config', (route) =>
    route.fulfill({
      status: 503,
      headers: { 'X-Request-ID': 'offline-upstream' },
      body: 'Unavailable',
    }),
  );
  await page.getByRole('button', { name: 'Save changes', exact: true }).click();
  await expect(page.locator('#notice')).toContainText('offline-upstream');
  await expect(page.getByRole('button', { name: 'Reload latest' })).toBeHidden();
  await page.route('**/config', (route) =>
    route.fulfill({
      status: 409,
      json: { code: 'revision_conflict', error: 'Concurrent change' },
    }),
  );
  await page.getByRole('button', { name: 'Save changes', exact: true }).click();
  const reload = page.getByRole('button', { name: 'Reload latest' });
  page.once('dialog', (dialog) => dialog.dismiss());
  await reload.click();
  await expect(name).toHaveValue('Keep through failure');
  page.once('dialog', (dialog) => dialog.accept());
  await reload.click();
  await expect(name).toHaveValue('Evening games');
  await expect(page.locator('#savebar')).toBeHidden();
});

test('returning from Discord rechecks saved readiness while preserving a conflicting draft', async ({
  page,
}) => {
  const state = await mockApp(page);
  state.access = 'unavailable';
  await page.goto(`/?community=${guild}&view=setup`);
  await page.locator('.game-settings > summary').click();
  await page.getByRole('textbox', { name: 'Game name', exact: true }).fill('Local game name');
  // Keep the test offline while exercising the actual invite click and return listeners.
  await page.evaluate(() => {
    document.addEventListener('click', (event) => {
      if ((event.target as Element).closest('[data-discord-invite]')) event.preventDefault();
    });
  });
  await page.getByRole('link', { name: 'Add to Discord', exact: true }).click();
  state.access = 'ready';
  state.snapshot.revision++;
  state.snapshot.settings.games[0].name = 'Saved in another tab';
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect(page.getByRole('button', { name: 'Reload latest' })).toBeVisible();
  await expect(page.getByRole('textbox', { name: 'Game name', exact: true })).toHaveValue(
    'Local game name',
  );
  await expect(page.getByRole('heading', { name: 'Ready to play', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Check access again', exact: true }).click();
  await expect(page.getByRole('textbox', { name: 'Game name', exact: true })).toHaveValue(
    'Local game name',
  );
  await expect(page).toHaveURL(new RegExp(`game=${game}`));
});

test('a late profile response cannot replace the page selected with Back', async ({ page }) => {
  await mockApp(page);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => (release = resolve));
  let requested!: () => void;
  const started = new Promise<void>((resolve) => (requested = resolve));
  await page.route('**/statistics/**', async (route) => {
    if (new URL(route.request().url()).searchParams.has('user')) {
      requested();
      await gate;
    }
    await route.fallback();
  });
  await page.goto(`/stats?community=${guild}&game=${game}`);
  await page.locator('[data-user]').first().click();
  await started;
  await page.goBack();
  await expect(page.locator('#app')).toHaveAttribute('aria-busy', 'false');
  const response = page.waitForResponse(
    (r) => r.url().includes('/statistics/') && r.url().includes('user='),
  );
  release();
  await response;
  await expect(page.locator('#player-statistics-heading')).toHaveCount(0);
  await expect(page.locator('[name=user]')).toHaveValue('');
});

test('dialogs contain keyboard focus and dismiss across rotation without losing edits', async ({
  page,
}) => {
  await mockApp(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(organizer);
  const name = page.getByRole('textbox', { name: 'Queue name', exact: true });
  await name.fill('Rotated draft');
  await page.getByRole('button', { name: 'Choose community or queue' }).click();
  for (let index = 0; index < 9; index++) {
    await page.keyboard.press('Tab');
    expect(await page.evaluate(() => Boolean(document.activeElement?.closest('dialog')))).toBe(
      true,
    );
  }
  await page.setViewportSize({ width: 844, height: 390 });
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(name).toHaveValue('Rotated draft');
  await page.getByRole('button', { name: 'More about Queue name', exact: true }).click();
  await page.mouse.click(1, 1);
  await expect(page.getByRole('dialog')).toHaveCount(0);
});

for (const width of [390, 1440]) {
  test(`public screens and import review at ${width}px`, async ({ page }, testInfo) => {
    await mockApp(page);
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    await page.goto(`/stats?community=${guild}&game=${game}`);
    await expect(page.getByRole('heading', { name: 'Leaderboard', exact: true })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath('leaderboard.png'), fullPage: true });
    await page.locator('[data-user]').first().click();
    for (const section of ['Overview', 'Matches', 'Rating history', 'Details']) {
      await page.getByRole('button', { name: section, exact: true }).click();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
        true,
      );
      await page.screenshot({
        path: testInfo.outputPath(`profile-${section.replaceAll(' ', '-')}.png`),
        fullPage: true,
      });
    }
    await page.goto(`/?community=${guild}&view=players`);
    await page.locator('[data-import-section] > summary').click();
    const form = page.locator('[data-kind="import-preview"]');
    await form.locator('[name=content]').fill(`id,name,rating,deviation\n${user},Player,1400,120`);
    await form.getByRole('button', { name: 'Preview import' }).click();
    await expect(page.getByRole('button', { name: 'Apply reviewed import' })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath('import-preview.png') });
  });
}

test('a fresh custom game and its first queue can be saved without a map pool', async ({
  page,
}) => {
  const state = await mockApp(page);
  state.snapshot.settings = { games: [], queues: [] };
  await page.goto(`/?community=${guild}&view=setup`);
  await page.getByRole('textbox', { name: 'Custom game name' }).fill('Friday games');
  await page.getByRole('button', { name: 'Add to changes' }).click();
  await page.getByRole('tab', { name: 'Queues', exact: true }).click();
  await page.getByRole('button', { name: 'Add queue', exact: true }).click();
  await page.getByRole('checkbox', { name: 'Enable queue', exact: true }).check();
  await page.getByRole('button', { name: 'Save changes', exact: true }).click();
  await expect(page.locator('#savebar')).toBeHidden();
  expect(state.saved).toHaveLength(1);
  expect(state.saved[0].queues[0]).toMatchObject({
    enabled: true,
    gameId: state.saved[0].games[0].id,
  });
  expect(state.saved[0].queues[0].maps).toBeUndefined();
  await page.getByRole('tab', { name: 'Setup', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Ready to play', exact: true })).toBeVisible();
});

test('rank rows and shared tax edits survive refresh and new queues inherit rating policy', async ({
  page,
}) => {
  const state = await mockApp(page);
  await page.goto(`/?community=${guild}&view=setup`);
  await page.locator('.game-settings > summary').click();
  await page.getByRole('button', { name: 'Add rank', exact: true }).click();
  await page.getByRole('textbox', { name: 'Rank label', exact: true }).last().fill('Champion');
  await page.getByRole('spinbutton', { name: 'Rating threshold', exact: true }).last().fill('2800');
  await page.getByRole('checkbox', { name: 'Show rating in nicknames', exact: true }).check();
  const rankRole = page
    .getByRole('combobox', { name: 'Cosmetic Discord role', exact: true })
    .last();
  await rankRole.selectOption('100000000000000003');
  await page.getByRole('spinbutton', { name: 'Starting rating', exact: true }).fill('1234');
  await page
    .getByRole('spinbutton', { name: 'Thursday tax time (UTC minutes)', exact: true })
    .fill('780');
  await page.getByRole('button', { name: 'Account', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Refresh', exact: true }).click();
  state.rolesAvailable = false;
  await page.getByRole('button', { name: 'Check access again', exact: true }).click();
  await expect(rankRole).toHaveValue('100000000000000003');
  await expect(rankRole.locator('option:checked')).toContainText('Unavailable');
  await rankRole.selectOption('');
  await expect(
    page.getByRole('checkbox', { name: 'Show rating in nicknames', exact: true }).locator('..'),
  ).toContainText('On');
  await expect(page.getByRole('textbox', { name: 'Rank label', exact: true }).last()).toHaveValue(
    'Champion',
  );
  await page.getByRole('tab', { name: 'Queues', exact: true }).click();
  await page.getByRole('button', { name: 'Add queue', exact: true }).click();
  await page.getByRole('button', { name: 'Save changes', exact: true }).click();
  await expect(page.locator('#savebar')).toBeHidden();
  expect(state.saved[0].queues.map((q) => q.ratingSettings?.initial)).toEqual([1234, 1234]);
  expect(state.saved[0].queues.map((q) => q.economy?.taxMinuteUtc)).toEqual([780, 780]);
  expect(state.saved[0].games[0].records?.ranks.at(-1)?.label).toBe('Champion');
});

test('a 500-player import remains reviewable, duplicate imports have no Apply action', async ({
  page,
}) => {
  const state = await mockApp(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/?community=${guild}&view=players`);
  await page.locator('[data-import-section] > summary').click();
  const form = page.locator('[data-kind="import-preview"]');
  const rows = Array.from(
    { length: 500 },
    (_, i) => `${100000000000000000n + BigInt(i)},Player ${i},1400,120`,
  );
  await form.locator('[name=file]').setInputFiles({
    name: 'players.csv',
    mimeType: 'text/csv',
    buffer: Buffer.from(`id,name,rating,deviation\n${rows.join('\n')}`),
  });
  await form.getByRole('button', { name: 'Preview import' }).click();
  await page.locator('.import-rows > summary').click();
  const reviewed = new Set<string>();
  for (let batch = 0; batch < 10; batch++) {
    await expect(page.locator('[data-import-range]')).toContainText(
      `Showing ${batch * 50 + 1}–${(batch + 1) * 50} of 500`,
    );
    await expect(page.locator('.import-player-card')).toHaveCount(50);
    for (const name of await page.locator('.import-player-card h4').allTextContents())
      reviewed.add(name);
    if (batch < 9)
      await page.getByRole('button', { name: 'Next player records', exact: true }).click();
  }
  expect(reviewed.size).toBe(500);
  expect(reviewed.has('Player 499')).toBe(true);
  await expect(
    page.getByRole('button', { name: 'Next player records', exact: true }),
  ).toBeDisabled();
  await page.locator('.import-player-card').last().scrollIntoViewIfNeeded();
  await expect(page.getByRole('button', { name: 'Apply reviewed import' })).toBeInViewport();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  state.importApplied = true;
  await form.getByRole('button', { name: 'Preview import' }).click();
  await expect(page.getByRole('heading', { name: 'Already imported' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Apply reviewed import' })).toHaveCount(0);
});

for (const [width, height] of [
  [360, 780],
  [390, 844],
  [430, 932],
  [640, 900],
  [641, 900],
  [768, 1024],
  [844, 390],
  [900, 900],
  [901, 900],
  [1440, 1000],
]) {
  test(`screens remain reachable at ${width}×${height}`, async ({ page }, testInfo) => {
    await mockApp(page);
    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await page.setViewportSize({ width, height });
    await page.goto(organizer);
    await page
      .getByRole('textbox', { name: 'Queue name', exact: true })
      .fill('A queue with a longer name');
    for (const view of ['Queues', 'Matches', 'Players', 'Setup']) {
      await page.getByRole('tab', { name: view, exact: true }).click();
      await page.locator('.workspace-scroll').evaluate((el) => (el.scrollTop = 0));
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
        true,
      );
      await expect(
        page.getByRole('button', { name: 'Save changes', exact: true }),
      ).toBeInViewport();
      if (width === 390 || width === 1440)
        await page.screenshot({ path: testInfo.outputPath(`${view.toLowerCase()}-${width}.png`) });
    }
    await page.getByRole('tab', { name: 'Queues', exact: true }).click();
    const targets = await page
      .locator('[data-category], [data-help]:visible, [data-reset]:visible')
      .evaluateAll((els) =>
        els.map((el) => ({
          width: el.getBoundingClientRect().width,
          height: el.getBoundingClientRect().height,
        })),
      );
    expect(targets.every((r) => r.width >= 44 && r.height >= 44)).toBe(true);
    await page.getByRole('button', { name: 'Voice / Rally', exact: true }).click();
    await page
      .getByRole('spinbutton', { name: 'Maximum mentions', exact: true })
      .scrollIntoViewIfNeeded();
    await expect(
      page.getByRole('spinbutton', { name: 'Maximum mentions', exact: true }),
    ).toBeInViewport();
    expect(errors).toEqual([]);
  });
}

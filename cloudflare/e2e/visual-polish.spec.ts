import { test, expect, type Page } from '@playwright/test';
import { mockApp, guild, queue, game, user } from './fixtures';

/** Richer, explicitly illustrative records for reviewing the real screens, never a live community. */
async function visualApp(page: Page) {
  const state = await mockApp({
    route: async (pattern: string, handler: (route: unknown) => Promise<unknown>) =>
      page.route(pattern, async (route) =>
        handler({
          request: () => route.request(),
          fulfill: async (options: { json?: any; status?: number; body?: string }) => {
            const data = options.json;
            if (data?.communities) data.communities[0].name = 'Demo game night';
            const records = data?.records ?? (data?.players ? data : undefined);
            if (records?.players?.length >= 8 && records.matches?.[0]) {
              const names = [
                'Alex',
                'Sam',
                'Rin',
                'Jo',
                'Kai',
                'Dev',
                'Robin',
                'Taylor',
                'Morgan',
                'Casey',
                'Jamie',
                'Sky',
              ];
              records.players.forEach((p: any, i: number) => {
                p.name = names[i] ?? p.name;
                p.rating = 1680 - i * 43;
              });
              const players = records.players.slice(0, 8);
              records.matches[0] = {
                ...records.matches[0],
                roster: players,
                teams: [players.slice(0, 4), players.slice(4)],
                captains: [players[0].id, players[4].id],
                ready: players.slice(0, 6).map((p: any) => p.id),
              };
              records.waiting = [
                {
                  queueId: queue,
                  players: records.players.slice(8).map((p: any) => ({ id: p.id, name: p.name })),
                },
              ];
            }
            return route.fulfill(options);
          },
        }),
      ),
  } as unknown as Page);
  state.playerCount = 12;
  state.playerName = 'Alex';
  state.snapshot.settings.queues.push({
    ...structuredClone(state.snapshot.settings.queues[0]),
    id: '81da571b-6a4a-4e5d-8e32-f4050df35a2e',
    name: 'Weekend duels',
    enabled: false,
    size: 2,
    isDefault: false,
  });
  return state;
}

for (const width of [390, 768, 1440]) {
  test(`review every console surface in both themes at ${width}px`, async ({ page }, info) => {
    await visualApp(page);
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    await page.goto(`/app/?community=${guild}&view=queues`);
    if (width === 390) {
      const badge = await page.locator('#tab-matches .nav-count').boundingBox();
      const tab = await page.locator('#tab-matches').boundingBox();
      expect(badge && tab && badge.y >= tab.y && badge.y + badge.height <= tab.y + tab.height).toBe(
        true,
      );
    }
    for (const theme of ['light', 'dark']) {
      await page.getByRole('button', { name: 'Account', exact: true }).click();
      await page.getByRole('dialog').getByLabel('Theme').selectOption(theme);
      await page.keyboard.press('Escape');
      for (const view of ['queues', 'matches', 'players', 'setup']) {
        await page
          .getByRole('tab', { name: view[0].toUpperCase() + view.slice(1), exact: true })
          .click();
        await page.locator('.workspace-scroll').evaluate((el) => (el.scrollTop = 0));
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
          true,
        );
        await page.screenshot({ path: info.outputPath(`${view}-${theme}-${width}.png`) });
      }
      await page.getByRole('tab', { name: 'Queues', exact: true }).click();
      await page.getByRole('button', { name: 'Settings for Evening games', exact: true }).click();
      await expect(page.locator('[data-queue-list]')).toBeVisible();
      await page.locator('.workspace-scroll').evaluate((el) => (el.scrollTop = 0));
      await page.screenshot({ path: info.outputPath(`queue-editor-${theme}-${width}.png`) });
      await page.getByRole('textbox', { name: 'Queue name', exact: true }).fill('Friday games');
      await page.getByRole('button', { name: 'Review changes', exact: true }).click();
      await expect(page.getByRole('dialog')).toContainText('Changes');
      await page.screenshot({ path: info.outputPath(`review-${theme}-${width}.png`) });
      await page.getByRole('button', { name: 'Back to editing', exact: true }).click();
      await page.locator('[data-queue-list]').click();
      await expect(page.locator('.queue-card').first()).toContainText('Friday games');
      await page.getByRole('button', { name: 'Settings for Friday games', exact: true }).click();
      await expect(page.getByRole('textbox', { name: 'Queue name', exact: true })).toHaveValue(
        'Friday games',
      );
      await page.getByRole('textbox', { name: 'Queue name', exact: true }).fill('Evening games');
      await page.locator('[data-queue-list]').click();
      await expect(page.locator('#savebar')).toBeHidden();
    }
  });
}

test('direct match result keeps confirmation and rejects a changed choice until reviewed again', async ({
  page,
}) => {
  const state = await mockApp(page);
  await page.goto(`/app/?community=${guild}&view=matches`);
  const form = page.locator('.result-form');
  await form.getByRole('radio', { name: 'Beta won', exact: true }).check();
  await expect(form.locator('[data-result-consequence]')).toContainText('Record Beta');
  await form.getByRole('button', { name: 'Review result', exact: true }).click();
  expect(state.actions).toHaveLength(0);
  await expect(form.locator('[data-review]')).toContainText('beta');
  await form.getByRole('radio', { name: 'Draw', exact: true }).check();
  await form.getByRole('button', { name: 'Review result', exact: true }).click();
  expect(state.actions).toHaveLength(0);
  await expect(form.locator('[data-review]')).toContainText('draw');
  await form.getByRole('button', { name: 'Confirm action', exact: true }).click();
  await expect.poll(() => state.actions.length).toBe(1);
  expect(state.actions[0]).toMatchObject({
    kind: 'admin_report',
    matchId: 'match-one',
    version: 1,
    result: 'draw',
  });
});

test('public profiles and imports retain all sections in the refined layouts', async ({
  page,
}, info) => {
  await visualApp(page);
  for (const width of [390, 1440]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    await page.goto(`/stats?community=${guild}&game=${game}&user=${user}`);
    for (const section of ['Overview', 'Matches', 'Rating history', 'Details']) {
      await page.getByRole('button', { name: section, exact: true }).click();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
        true,
      );
      await page.screenshot({
        path: info.outputPath(`statistics-${section.replace(' ', '-')}-${width}.png`),
        fullPage: true,
      });
    }
    await page.goto(`/app/?community=${guild}&view=players`);
    await page.locator('[data-import-section]>summary').click();
    const form = page.locator('[data-kind=import-preview]');
    await form
      .locator('[name=content]')
      .fill(`id,name,rating,deviation\n${user},Demo player,1400,120`);
    await form.getByRole('button', { name: 'Preview import', exact: true }).click();
    await expect(
      page.getByRole('button', { name: 'Apply reviewed import', exact: true }),
    ).toBeVisible();
    await page.locator('[data-import-section]').scrollIntoViewIfNeeded();
    expect(
      await page.evaluate(() => document.documentElement.scrollHeight <= innerHeight + 1),
    ).toBe(true);
    await page.screenshot({ path: info.outputPath(`import-${width}.png`), fullPage: true });
  }
});

test('every public page remains legible at phone, tablet and desktop sizes', async ({
  page,
}, info) => {
  test.setTimeout(90000);
  for (const width of [360, 768, 1440]) {
    await page.setViewportSize({ width, height: width === 360 ? 780 : 1000 });
    for (const path of [
      '/',
      '/games/',
      '/games/age-of-empires-2/',
      '/games/age-of-empires-2/setup/',
      '/features/',
      '/guides/',
      '/about/',
      '/open-source/',
      '/privacy/',
      '/terms/',
      '/changelog/',
    ]) {
      await page.goto(path);
      await expect(page.locator('h1')).toHaveCount(1);
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
        path,
      ).toBe(true);
      await page.locator('.dashboard-section img').evaluateAll(async (images) => {
        for (const img of images) {
          (img as HTMLImageElement).loading = 'eager';
          await (img as HTMLImageElement).decode();
        }
      });
      await page.screenshot({
        path: info.outputPath(`public-${path.replaceAll('/', '-')}-${width}.png`),
        fullPage: true,
      });
    }
  }
});

for (const width of [901, 1024, 1280, 1440]) {
  test(`desktop top navigation never overlaps at ${width}px`, async ({ page }) => {
    await mockApp(page);
    await page.setViewportSize({ width, height: 900 });
    await page.goto(`/app/?community=${guild}&view=queues`);
    for (const selector of [
      '#community',
      '#tab-queues',
      '#tab-matches',
      '#tab-players',
      '#tab-setup',
      '.context-statistics',
      '[data-account-menu]',
    ]) {
      const control = page.locator(selector);
      await expect(control).toBeInViewport();
      expect(
        await control.evaluate((el) => {
          const r = el.getBoundingClientRect();
          const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
          return el === hit || el.contains(hit);
        }),
        selector,
      ).toBe(true);
    }
  });
}

for (const width of [390, 1440]) {
  test(`review waiting, game choice, first queue and completed guide at ${width}px`, async ({
    page,
  }, info) => {
    const state = await mockApp(page);
    state.snapshot.settings = { games: [], queues: [] };
    state.access = 'needs_access';
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    await page.goto(`/app/?community=${guild}&view=setup`);
    await page.getByRole('button', { name: 'Start guided setup', exact: true }).click();
    await page.screenshot({ path: info.outputPath('guide-access-waiting.png') });
    state.access = 'ready';
    await page.getByRole('button', { name: 'I’m back — check access', exact: true }).click();
    await page.getByRole('button', { name: 'Choose game', exact: true }).click();
    await page.screenshot({ path: info.outputPath('guide-game.png') });
    await page.locator('[data-game-choice="catalog:aoe2"]').click();
    await page.getByRole('button', { name: 'Create queue draft', exact: true }).click();
    await expect(page.locator('[data-guide-queue]')).toBeVisible();
    await page.screenshot({ path: info.outputPath('guide-queue.png') });
    await page.getByRole('button', { name: 'Review and save', exact: true }).click();
    await expect(page.getByRole('dialog')).toContainText('Adds');
    await expect(page.getByRole('dialog')).toContainText('Creates');
    await page.screenshot({ path: info.outputPath('guide-review.png') });
    await page.getByRole('button', { name: 'Save reviewed changes', exact: true }).click();
    await expect(
      page.getByRole('heading', { name: 'Try your queue in Discord', exact: true }),
    ).toBeVisible();
    await page.screenshot({ path: info.outputPath('guide-complete.png') });
    expect(state.saved).toHaveLength(1);
  });
}

test('player shortcuts work without loaded players and keep the selected game explicit', async ({
  page,
}) => {
  const state = await mockApp(page);
  state.playerCount = 0;
  await page.goto(`/app/?community=${guild}&view=players`);
  await page
    .getByRole('combobox', { name: 'Filter players by game', exact: true })
    .selectOption('');
  await expect(
    page.getByRole('button', { name: 'Leaderboard and ranks', exact: true }),
  ).toBeDisabled();
  await page
    .getByRole('combobox', { name: 'Filter players by game', exact: true })
    .selectOption(game);
  await page.getByRole('button', { name: 'Leaderboard and ranks', exact: true }).click();
  await expect(
    page.getByRole('combobox', { name: 'Statistics visibility', exact: true }),
  ).toBeVisible();
  await page.getByRole('tab', { name: 'Players', exact: true }).click();
  await page.getByRole('button', { name: 'Import', exact: true }).click();
  await expect(page.locator('[data-import-section]')).toHaveAttribute('open', '');
  await expect(page.getByRole('button', { name: 'Preview import', exact: true })).toBeVisible();
});

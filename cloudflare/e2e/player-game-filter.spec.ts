import { expect, test } from '@playwright/test';
import { guild, game, mockApp } from './fixtures';

test('the default player game picker excludes queue-less test ladders and preserves opt-in history', async ({
  page,
}) => {
  const state = await mockApp(page);
  const historical = 'aaaaaaaa-1111-4111-8111-111111111111';
  state.snapshot.settings.games.unshift({
    id: historical,
    name: '8P Review Glicko2',
    catalogId: null,
  });
  const loaded = page.waitForResponse((response) =>
    new URL(response.url()).pathname.endsWith('/setup'),
  );
  await page.goto(`/?community=${guild}&view=players`);
  const initial = await (await loaded).json();
  await page.route('**/api/communities/*/records', (route) =>
    route.fulfill({
      json: {
        ...initial.records,
        playersTotal: 2,
        players: [
          ...initial.records.players,
          {
            ...initial.records.players[0],
            id: '100000000000000099',
            name: 'Historical test player',
            gameId: historical,
          },
        ],
      },
    }),
  );
  await page.getByRole('button', { name: 'Account', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Refresh', exact: true }).click();
  const select = page.getByRole('combobox', { name: 'Filter players by game' });
  await expect(select).toHaveValue(game);
  await expect(select.locator('option')).toHaveText(['All games', 'Age of Empires II']);
  await select.selectOption('');
  await expect(page.locator('[data-rating-row]:visible')).toHaveCount(1);
  const include = page.getByRole('checkbox', { name: 'Include games without queues' });
  await include.check();
  await expect(page.locator('[data-rating-row]:visible')).toHaveCount(2);
  await expect(select.locator('option')).toContainText([
    'All games',
    '8P Review Glicko2',
    'Age of Empires II',
  ]);
  await select.selectOption(historical);
  await expect(page.locator('[data-rating-row]:visible')).toContainText('Historical test player');
  await page.getByRole('tab', { name: 'Matches', exact: true }).click();
  await page.getByRole('tab', { name: 'Players', exact: true }).click();
  await expect(include).toBeChecked();
  await expect(select).toHaveValue(historical);
  await include.uncheck();
  await expect(select).toHaveValue(game);
  await expect(page.locator('[data-rating-row]:visible')).toHaveCount(1);
  expect(state.saved).toHaveLength(0);
  expect(state.snapshot.settings.games).toHaveLength(2);
});

test('custom games with a paused queue remain normal choices', async ({ page }) => {
  const state = await mockApp(page);
  state.snapshot.settings.games[0].catalogId = null;
  state.snapshot.settings.games[0].name = 'Community board game';
  state.snapshot.settings.queues[0].enabled = false;
  await page.goto(`/?community=${guild}&view=players`);
  const select = page.getByRole('combobox', { name: 'Filter players by game' });
  await expect(select.locator('option')).toHaveText(['All games', 'Community board game']);
  await select.selectOption('');
  await page.getByRole('button', { name: 'Account', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(select).toHaveValue('');
  await expect(page.locator('[data-rating-row]:visible')).toHaveCount(1);
});

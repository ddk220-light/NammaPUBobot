import { expect, test, type Page } from '@playwright/test';
import { gameCapabilities, selectableCatalog } from '../src/games/catalog';
import { mockApp, guild, game, user } from './fixtures';

async function connectorApp(
  page: Page,
  catalogId: 'wc3' | 'bar' | 'zero_hour' | 'northgard' = 'wc3',
) {
  const state = await mockApp(page);
  const catalog = gameCapabilities(catalogId)!;
  state.snapshot.settings.games[0].catalogId = catalogId;
  state.snapshot.settings.games[0].name = catalog.name;
  await page.route('**/api/communities/*/connectors', (route) =>
    route.fulfill({
      json: {
        games: [
          {
            gameId: game,
            catalogId,
            unresolvedBindings: 0,
            capabilities: {
              ...catalog.manifest.capabilities,
              lobbyObservation: 'acceptance_required',
            },
          },
        ],
      },
    }),
  );
  return state;
}
const gamePanel = (page: Page) => page.locator(`#game-settings-${game}`);
async function openGame(page: Page) {
  await page.goto(`/?community=${guild}&view=setup&game=${game}`);
  await gamePanel(page).locator('.connector-settings > summary').click();
}

test('both game pickers offer the seven-game scope and label conditional access', async ({
  page,
}) => {
  const state = await mockApp(page);
  state.snapshot.settings = { games: [], queues: [] };
  await page.goto(`/?community=${guild}&view=setup`);
  await expect(page.locator('[data-add-game] [name="catalogId"] option')).toHaveCount(8);
  const choices = await page
    .locator('[data-add-game] [name="catalogId"] option')
    .evaluateAll((options) => options.map((o) => (o as HTMLOptionElement).value).filter(Boolean));
  expect(choices.sort()).toEqual(selectableCatalog.map((g) => g.id).sort());
  await expect(page.locator('[data-add-game] option[value="zero_hour"]')).toContainText(
    'provider access required',
  );
  await page.getByRole('button', { name: 'Start guided setup', exact: true }).click();
  await page.getByRole('button', { name: 'Choose game', exact: true }).click();
  const guideChoices = await page.locator('[data-guide-game] option').evaluateAll((options) =>
    options
      .map((o) => (o as HTMLOptionElement).value)
      .filter((v) => v.startsWith('catalog:'))
      .map((v) => v.slice(8)),
  );
  expect(guideChoices.sort()).toEqual(choices.sort());
  await page.getByLabel('Game for guided setup').selectOption('catalog:wc3');
  await page.getByRole('button', { name: 'Create queue draft' }).click();
  await page.getByRole('button', { name: 'Review and save' }).click();
  await page.getByRole('button', { name: 'Save reviewed changes' }).click();
  await expect(page.locator('#savebar')).toBeHidden();
  expect(state.saved[0].games[0].catalogId).toBe('wc3');
});

test('connector capabilities and queue edits survive refresh and failed atomic saves', async ({
  page,
}) => {
  const state = await connectorApp(page);
  await openGame(page);
  const panel = gamePanel(page);
  await expect(panel).toContainText('Awaiting game acceptance');
  await panel.locator('[name="lobbyConnector"]').check();
  await panel.locator('[name="capability-matchLookup"]').uncheck();
  await panel.getByRole('checkbox', { name: 'Ongoing matchmaking games', exact: false }).uncheck();
  await page.getByRole('tab', { name: 'Queues', exact: true }).click();
  await page.getByRole('textbox', { name: 'Queue name', exact: true }).fill('Combined draft');
  await page.getByRole('button', { name: 'Refresh records' }).click();
  await page.getByRole('tab', { name: 'Setup', exact: true }).click();
  await panel.locator('.connector-settings > summary').click();
  await expect(panel.locator('[name="capability-matchLookup"]')).not.toBeChecked();
  state.failSave = 'revision_conflict';
  await page.locator('[data-edit="save"]').click();
  await expect(page.locator('#savebar')).toBeVisible();
  await expect(panel.locator('[name="lobbyConnector"]')).toBeChecked();
  state.failSave = '';
  await page.locator('[data-edit="save"]').click();
  await expect(page.locator('#savebar')).toBeHidden();
  expect(state.saved.at(-1)!.games[0].connector).toMatchObject({
    enabled: true,
    capabilities: { matchLookup: false, ongoingDiscovery: false, accountLookup: true },
  });
  expect(state.saved.at(-1)!.queues[0].name).toBe('Combined draft');
});

test('public-game browsing uses the saved game endpoint without discarding a draft', async ({
  page,
}, info) => {
  await connectorApp(page);
  await page.setViewportSize({ width: 390, height: 844 });
  const requests: string[] = [];
  await page.route('**/api/communities/*/connectors/*/games?*', (route) => {
    requests.push(route.request().url());
    return route.fulfill({
      json: {
        status: 'ok',
        value: {
          checkedAt: Date.now(),
          phase: 'running',
          coverage: 'bounded-snapshot',
          games: [
            {
              externalId: 'original-game',
              referenceKind: 'match',
              provider: 'w3champions',
              source: 'w3champions',
              name: 'Ranked game',
              phase: 'started',
              observedAt: Date.now(),
              map: 'Tidehunters',
              players: [
                { profileId: 'Fixture1#1001', name: 'Fixture1', team: '1', selection: 'Human' },
                { profileId: 'Fixture2#1002', name: 'Fixture2', team: '2', selection: 'Orc' },
              ],
            },
          ],
        },
      },
    });
  });
  await openGame(page);
  await gamePanel(page)
    .getByRole('textbox', { name: 'Game name', exact: true })
    .fill('My Warcraft group');
  await gamePanel(page).getByRole('button', { name: 'Show running games' }).click();
  await expect(gamePanel(page).locator('[data-game-list]')).toContainText('Fixture1 (Human)');
  expect(new URL(requests[0]).pathname).toBe(`/api/communities/${guild}/connectors/${game}/games`);
  await expect(page.locator('#savebar')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath('warcraft-connector-phone.png') });
});

test('retired games remain editable but are excluded from new setup', async ({ page }) => {
  const state = await connectorApp(page, 'northgard');
  await openGame(page);
  await expect(page.locator('[data-add-game] option[value="northgard"]')).toHaveCount(0);
  await expect(gamePanel(page).locator('[name="lobbyConnector"]')).toHaveCount(0);
  await gamePanel(page).locator('[name="capability-ownershipVerification"]').uncheck();
  await page.locator('[data-edit="save"]').click();
  await expect(page.locator('#savebar')).toBeHidden();
  expect(state.saved[0].games[0]).toMatchObject({
    catalogId: 'northgard',
    connector: { enabled: false, capabilities: { ownershipVerification: false } },
  });
});

test('member account linking avoids organizer discovery and preserves error metadata', async ({
  page,
}) => {
  await mockApp(page);
  let organizerReads = 0;
  page.on('request', (request) => {
    if (new URL(request.url()).pathname === '/api/communities') organizerReads++;
  });
  await page.route('**/auth/game/start', (route) => {
    expect(route.request().headers()['x-csrf-token']).toBe('test-csrf');
    expect(route.request().postDataJSON()).toEqual({
      communityId: guild,
      gameId: game,
      provider: 'battlenet',
    });
    return route.fulfill({
      status: 503,
      json: {
        code: 'not_configured',
        error: 'Provider verification unavailable.',
        requestId: 'verify-request',
      },
    });
  });
  await page.goto(`/link?community=${guild}&game=${game}&provider=battlenet`);
  await page.getByRole('button', { name: 'Continue to Battle.net' }).click();
  await expect(page.getByRole('alert')).toContainText('verify-request');
  expect(organizerReads).toBe(0);
  await expect(page.getByRole('tablist', { name: 'Organizer sections' })).toHaveCount(0);
});

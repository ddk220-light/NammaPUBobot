import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
// Start Vite locally before running this optional, entirely mocked browser check.
const baseUrl = process.env.CONNECTOR_UI_BASE_URL ?? 'http://127.0.0.1:4197';
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1100, height: 900 } });
const errors = [];
page.on('pageerror', (error) => errors.push(error.message));
const guild = '706320010368516198',
  game = '4daebc0a-55e8-4d56-a17d-d94913644910',
  zh = '4daebc0a-55e8-4d56-a17d-d94913644911';
let signedIn = true,
  communityReads = 0,
  verificationRequest,
  savedSettings;
let snapshot = {
  communityId: guild,
  revision: 1,
  settings: {
    games: [
      { id: game, name: 'Northgard', catalogId: 'northgard' },
      { id: zh, name: 'Zero Hour', catalogId: 'zero_hour', connector: { enabled: false } },
    ],
    queues: [],
  },
};
const records = {
  economy: { wallets: [], ledger: [], books: [], taxRuns: [] },
  onboarding: {},
  pendingReturns: [],
  matches: [],
  players: [],
  playersTotal: 0,
  playersPage: 0,
  delivery: [],
  waiting: [],
  noadds: [],
  audit: [],
};
const capabilities = {
  accountLookup: 'unsupported',
  ownershipVerification: 'available',
  lobbyDiscovery: 'unsupported',
  lobbyObservation: 'unsupported',
  matchLookup: 'unsupported',
  historyLookup: 'unsupported',
  replayReferences: 'unsupported',
};
await page.route('**/api/**', async (route) => {
  const request = route.request(),
    path = new URL(request.url()).pathname;
  let body;
  if (path === '/api/bootstrap')
    body = {
      environment: 'local',
      version: '0.19.0',
      oauthReady: true,
      discordReady: true,
      inviteUrl: null,
      catalog: [
        { id: 'northgard', name: 'Northgard' },
        { id: 'zero_hour', name: 'Zero Hour' },
      ],
      user: signedIn ? { id: '100000000000000100', username: 'Member' } : null,
      csrf: signedIn ? 'fixture-csrf' : null,
    };
  else if (path === '/api/communities') {
    communityReads++;
    body = { communities: [{ id: guild, name: 'Connector test community' }] };
  } else if (path.endsWith('/setup'))
    body = { snapshot, channels: [], moderatorRoles: [], records };
  else if (path.endsWith('/connectors'))
    body = {
      games: [
        { gameId: game, catalogId: 'northgard', capabilities, unresolvedBindings: 0 },
        {
          gameId: zh,
          catalogId: 'zero_hour',
          capabilities: {
            ...capabilities,
            ownershipVerification: 'unsupported',
            lobbyDiscovery: 'credentials_required',
            lobbyObservation: 'credentials_required',
            matchLookup: 'credentials_required',
            replayReferences: 'credentials_required',
          },
          unresolvedBindings: 1,
        },
      ],
    };
  else if (path.endsWith('/config') && request.method() === 'PUT') {
    savedSettings = request.postDataJSON().settings;
    snapshot = { ...snapshot, revision: 2, settings: savedSettings };
    body = snapshot;
  } else throw Error(`Unexpected API request ${path}`);
  await route.fulfill({ json: body });
});
await page.route('**/auth/game/start', async (route) => {
  verificationRequest = {
    body: route.request().postDataJSON(),
    csrf: route.request().headers()['x-csrf-token'],
  };
  await route.fulfill({
    json: {
      url: 'https://steamcommunity.com/openid/login?fixture=1',
      expiresAt: Date.now() + 600000,
    },
  });
});
await page.route('https://steamcommunity.com/**', (route) =>
  route.fulfill({ contentType: 'text/html', body: '<h1>Provider sign-in fixture</h1>' }),
);
await page.goto(`${baseUrl}/link?community=${guild}&game=${game}&provider=steam`);
await page.getByRole('button', { name: 'Continue to Steam' }).waitFor();
assert.equal(communityReads, 0, 'Member verification must not require organizer community access');
await page.getByRole('button', { name: 'Continue to Steam' }).click();
await page.getByRole('heading', { name: 'Provider sign-in fixture' }).waitFor();
assert.deepEqual(verificationRequest, {
  body: { communityId: guild, gameId: game, provider: 'steam' },
  csrf: 'fixture-csrf',
});
signedIn = false;
await page.goto(`${baseUrl}/link?community=${guild}&game=${game}&provider=steam`);
const signin = page.getByRole('link', { name: 'Sign in with Discord' });
await signin.waitFor();
assert.ok((await signin.getAttribute('href')).includes('returnTo=%2Flink%3F'));
signedIn = true;
await page.goto(`${baseUrl}/?community=${guild}&view=setup`);
await page.getByRole('heading', { name: 'Games', exact: true }).waitFor();
const northgard = page.locator(`form[data-kind=rename][data-id="${game}"]`);
const zeroHour = page.locator(`form[data-kind=rename][data-id="${zh}"]`);
await northgard.locator('summary').click();
await zeroHour.locator('summary').click();
assert.equal(await northgard.locator('[name=lobbyConnector]').count(), 0);
assert.equal(await zeroHour.locator('[name=lobbyConnector]').count(), 1);
assert.ok((await zeroHour.textContent()).includes('Credentials required'));
await northgard.locator('[name=capability-ownershipVerification]').uncheck();
await northgard.getByRole('button', { name: 'Save game' }).click();
await page.getByText('Settings saved.', { exact: true }).waitFor();
assert.equal(savedSettings.games[0].connector.capabilities.ownershipVerification, false);
assert.equal(savedSettings.games[0].connector.enabled, false);
await page.setViewportSize({ width: 375, height: 850 });
await page.locator(`form[data-kind=rename][data-id="${game}"] summary`).click();
assert.ok(
  await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2),
  'Mobile setup should not overflow horizontally',
);
if (process.env.CONNECTOR_UI_SCREENSHOT)
  await page.screenshot({ path: process.env.CONNECTOR_UI_SCREENSHOT, fullPage: true });
assert.deepEqual(errors, []);
await browser.close();
console.log(
  'Browser smoke passed: member verification, CSRF forwarding, sign-in return path, capability diagnostics, independent setting persistence, mobile layout. All API/provider requests mocked.',
);

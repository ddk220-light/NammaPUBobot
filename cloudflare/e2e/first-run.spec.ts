import { expect, test, type Page } from '@playwright/test';
import { mockApp, guild } from './fixtures';

const channel = '706320010368516200';
async function freshServer(page: Page, access = 'needs_access') {
  const state = await mockApp(page);
  state.snapshot.settings = { games: [], queues: [] };
  state.access = access;
  return state;
}

test('a new server lands in the guide and Add to Discord is the one primary step', async ({
  page,
}) => {
  await freshServer(page);
  await page.goto(`/app/?community=${guild}`);
  await expect(page).toHaveURL(/view=setup/);
  await expect(page).toHaveURL(/guide=access/);
  const guide = page.locator('.guided-setup');
  await expect(guide).toContainText('The bot is not in this server yet');
  const add = guide.getByRole('link', { name: 'Add to Discord', exact: true });
  await expect(add).toHaveClass(/primary/);
  const href = new URL((await add.getAttribute('href'))!, 'https://example.test');
  expect(href.pathname).toBe('/auth/login');
  expect(href.searchParams.get('install')).toBe('1');
  expect(href.searchParams.get('guild')).toBe(guild);
  expect(href.searchParams.get('returnTo')).toContain(`community=${guild}`);
  await expect(add).not.toHaveAttribute('target', '_blank');
  await expect(guide.getByRole('button', { name: 'Choose game', exact: true })).not.toHaveClass(
    /primary/,
  );
});

test('without the bot a queue cannot reach save and never asks for a Discord ID', async ({
  page,
}) => {
  const state = await freshServer(page);
  await page.goto(`/app/?community=${guild}`);
  await page.getByRole('button', { name: 'Choose game', exact: true }).click();
  await page.locator('[data-game-choice="catalog:aoe2"]').click();
  await page.getByRole('button', { name: 'Create queue draft' }).click();
  const blocked = page.locator('[data-channel-blocked]');
  await expect(blocked).toHaveText('Add the bot to your server first, then choose the channel.');
  await expect(page.getByRole('button', { name: 'Review and save' })).toBeDisabled();
  await expect(page.getByRole('combobox', { name: 'Discord channel' })).toBeHidden();
  await page.getByRole('button', { name: 'Save changes' }).click();
  await expect(page.locator('#notice')).toContainText('Add the bot to your server first');
  await expect(page.locator('body')).not.toContainText('Enter a Discord ID');
  await expect(page).toHaveURL(/guide=queue/);
  expect(state.saved).toHaveLength(0);
});

test('the queues view of an empty server offers one clear next step', async ({ page }) => {
  await freshServer(page, 'ready');
  await page.goto(`/app/?community=${guild}&view=queues`);
  const empty = page.locator('[data-queue-empty]');
  await expect(empty.getByRole('heading', { name: 'Your first queue starts here' })).toBeVisible();
  await expect(page.locator('#view-queues')).not.toContainText('Setup needs attention');
  await expect(empty.getByRole('button')).toHaveCount(1);
  await empty.getByRole('button', { name: 'Start setup guide' }).click();
  await expect(page).toHaveURL(/view=setup/);
  await expect(page.locator('.guided-setup')).toContainText('Discord access verified');
});

test('a Discord outage is not presented as a missing bot', async ({ page }) => {
  const state = await mockApp(page);
  state.access = 'unavailable';
  state.retryAfter = '7';
  await page.goto(`/app/?community=${guild}&view=setup`);
  const setup = page.locator('#view-setup');
  await expect(setup.getByRole('heading', { name: 'Discord is not responding' })).toBeVisible();
  await expect(setup).toContainText('Try again in 7 seconds.');
  await expect(setup.getByRole('link', { name: 'Add to Discord' })).toHaveCount(0);
  await expect(setup.getByRole('link', { name: 'Invite bot to server' })).toHaveCount(0);
  await expect(setup).not.toContainText('Setup needs attention');
  await page.getByRole('button', { name: 'Start guided setup' }).click();
  await expect(page.locator('.guided-setup')).toContainText('Try again in 7 seconds.');
  await expect(page.locator('.guided-setup [data-discord-invite]')).toHaveCount(0);
  await page.getByRole('tab', { name: 'Queues', exact: true }).click();
  const queues = page.locator('#view-queues');
  await expect(queues.locator('[data-access-outage]')).toContainText('Try again in 7 seconds.');
  await expect(queues.getByRole('link', { name: 'Add to Discord' })).toHaveCount(0);
  state.access = 'ready';
  await queues.getByRole('button', { name: 'Check again' }).click();
  await expect(queues.locator('[data-access-outage]')).toHaveCount(0);
});

test('an expired session offers sign-in that returns to the same place with the draft', async ({
  page,
}) => {
  const state = await mockApp(page);
  await page.goto(`/app/?community=${guild}&view=setup`);
  await page.locator('.game-settings > summary').click();
  await page.getByRole('textbox', { name: 'Game name', exact: true }).fill('Kept while signed out');
  state.sessionExpired = true;
  await page.getByRole('button', { name: 'Save changes' }).click();
  const notice = page.locator('#notice');
  await expect(notice).toContainText('Your sign-in expired');
  await expect(notice).not.toContainText('Request');
  const link = notice.getByRole('link', { name: 'Sign in again' });
  const returnTo = new URL((await link.getAttribute('href'))!, page.url()).searchParams.get(
    'returnTo',
  )!;
  expect(returnTo).toContain(`community=${guild}`);
  expect(returnTo).toContain('view=setup');
  // Stand in for Discord: the sign-in round trip lands back on returnTo. WebKit cannot fulfill a
  // route with a redirect status, so the stand-in page navigates there itself.
  await page.route('**/auth/login**', (route) =>
    route.fulfill({
      contentType: 'text/html',
      body: `<meta http-equiv="refresh" content="0;url=${returnTo.replaceAll('&', '&amp;')}">`,
    }),
  );
  state.sessionExpired = false;
  await link.click();
  await expect(page.locator('#notice')).toContainText('Your unsaved changes are back');
  // returnTo kept the open game, so its settings are open again.
  await expect(page).toHaveURL(/game=/);
  await expect(page.getByRole('textbox', { name: 'Game name', exact: true })).toHaveValue(
    'Kept while signed out',
  );
  await expect(page.locator('#savebar')).toBeVisible();
  await page.getByRole('button', { name: 'Save changes' }).click();
  await expect(page.locator('#savebar')).toBeHidden();
  expect(state.saved.at(-1)!.games[0].name).toBe('Kept while signed out');
});

test('cancelling on Discord returns calmly to the console', async ({ page }) => {
  await mockApp(page);
  await page.goto(`/app/?community=${guild}&view=setup&login=cancelled`);
  await expect(page.locator('#notice')).toContainText('You left Discord before finishing');
  await expect(page).not.toHaveURL(/login=cancelled/);
});

test('signed-out visitors add the bot and sign in with one Discord trip', async ({ page }) => {
  const state = await mockApp(page);
  state.signedIn = false;
  await page.goto('/app/');
  await expect(page.getByRole('link', { name: 'Add to Discord', exact: true })).toHaveAttribute(
    'href',
    '/auth/login?install=1',
  );
});

test('the try step names and links the channel that holds the queue card', async ({ page }) => {
  await freshServer(page, 'ready');
  await page.goto(`/app/?community=${guild}`);
  await page.getByRole('button', { name: 'Choose game', exact: true }).click();
  await page.locator('[data-game-choice="catalog:aoe2"]').click();
  await page.getByRole('button', { name: 'Create queue draft' }).click();
  await page.getByRole('button', { name: 'Review and save' }).click();
  await page.getByRole('button', { name: 'Save reviewed changes' }).click();
  await expect(page.getByRole('heading', { name: 'Try your queue in Discord' })).toBeVisible();
  const guide = page.locator('.guided-setup');
  await expect(guide).toContainText('Its queue card is posted there');
  await expect(guide.getByRole('link', { name: 'Open #pickups in Discord' })).toHaveAttribute(
    'href',
    `https://discord.com/channels/${guild}/${channel}`,
  );
});

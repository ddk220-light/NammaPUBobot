import { test, expect } from '@playwright/test';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { mockApp, guild, queue } from './fixtures';
test('public content and the setup guide work without JavaScript', async ({ browser }, info) => {
  const context = await browser.newContext({ javaScriptEnabled: false });
  const page = await context.newPage();
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1 })).toContainText(
    'free Discord bot for RTS pickup games',
  );
  await expect(page.locator('main a[href="/invite"]').first()).toHaveClass(/\bbutton\b/);
  await page.getByRole('link', { name: 'Age of Empires II', exact: true }).first().click();
  await expect(page.locator('main .lead')).toContainText(
    'free, open-source Discord bot that runs Age of Empires II pickup games',
  );
  await expect(page.locator('main')).toContainText('Civ picks');
  await expect(page.locator('meta[name="robots"]')).toHaveAttribute('content', 'noindex');
  await page.getByRole('link', { name: 'five-step setup guide', exact: true }).click();
  await expect(page).toHaveURL(/\/guides\/getting-started\/$/);
  for (const step of [
    'Add the bot to your server',
    'Sign in to the console with Discord',
    'Pick your game',
    'Create a queue and choose its channel',
    'Players type /add',
  ])
    await expect(page.getByRole('heading', { level: 2, name: step, exact: true })).toBeVisible();
  await expect(page.locator('main')).toContainText('Report result');
  await expect(page.locator('.bot-example figcaption').first()).toContainText('Example');
  await page.screenshot({ path: info.outputPath('public-guide.png'), fullPage: true });
  await context.close();
});
test('old setup URLs land on the guide', async ({ page }) => {
  await page.goto('/games/warcraft-3/setup/');
  await expect(page).toHaveURL(/\/guides\/getting-started\/$/);
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Get started in five steps');
});
test('phones get a console link, no sideways scroll and readable dark text', async ({
  browser,
}) => {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
  await context.addInitScript(() => localStorage.setItem('cg-theme', 'dark'));
  const page = await context.newPage();
  for (const path of [
    '/',
    '/guides/getting-started/',
    '/guides/commands/',
    '/features/map-voting/',
  ]) {
    await page.goto(path);
    await expect(page.locator('header a[href="/app/"]')).toBeVisible();
    const box = await page.locator('header a[href="/app/"]').boundingBox();
    expect(box!.height).toBeGreaterThanOrEqual(44);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
      path,
    ).toBe(true);
  }
  await page.goto('/features/map-voting/');
  // WCAG AA for the subtitle in the dark theme, computed from the rendered colours.
  const ratio = await page.locator('.lead').evaluate((el) => {
    const rgb = (c: string) => c.match(/\d+/g)!.slice(0, 3).map(Number);
    const lum = (c: number[]) =>
      c
        .map((v) => ((v /= 255) <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4))
        .reduce((s, v, i) => s + v * [0.2126, 0.7152, 0.0722][i], 0);
    const a = lum(rgb(getComputedStyle(el).color)),
      b = lum(rgb(getComputedStyle(document.body).backgroundColor));
    return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
  });
  expect(ratio).toBeGreaterThanOrEqual(4.5);
  await context.close();
});
test('privacy, terms and about are real pages with a reachable contact section', async ({
  browser,
}) => {
  const context = await browser.newContext({ javaScriptEnabled: false });
  const page = await context.newPage();
  const required = {
    '/privacy/': [
      'Who runs this',
      'What we store and why',
      "What we don't do",
      'Who else is involved',
      'How long we keep it',
      'Your choices and how to delete',
      'Children',
      'Changes to this policy',
      'Contact',
    ],
    '/terms/': [
      'It is free',
      'What the service is',
      'Acceptable use',
      'Gold is play money',
      'Results and ratings',
      'No warranty',
      'Open source',
      'Changes to these terms',
      'Contact',
    ],
  };
  for (const [path, headings] of Object.entries(required)) {
    await page.goto(path);
    for (const name of headings)
      await expect(page.locator('main').getByRole('heading', { name, exact: true })).toHaveCount(1);
    await expect(page.locator('.legal-updated time')).toHaveAttribute(
      'datetime',
      /^\d{4}-\d{2}-\d{2}$/,
    );
  }
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Terms of use');
  for (const path of ['/', '/about/', '/privacy/', '/terms/', '/games/age-of-empires-2/']) {
    await page.goto(path);
    const text = await page.locator('body').innerText();
    expect(text, path).not.toMatch(/free hosting forever|preview does not promise/i);
    expect(text, path).not.toMatch(/NammaMatchBot|will be added when confirmed/);
  }
  await page.goto('/privacy/');
  await page.locator('footer').getByRole('link', { name: 'Contact', exact: true }).click();
  await expect(page).toHaveURL(/\/about\/#contact$/);
  await expect(page.locator('#contact').getByRole('link', { name: 'Open an issue' })).toBeVisible();
  await context.close();
});
test('an indexed build fails until the operator and private contact routes exist', async () => {
  const run = (env: Record<string, string>) =>
    promisify(execFile)(process.execPath, ['scripts/check-site.mjs'], {
      env: { ...process.env, ...env },
    });
  await expect(run({ PUBLIC_SITE_INDEXING: 'false' })).resolves.toBeTruthy();
  // src/identity.ts ships with these unset; the owner fills them in before launch.
  const { identity } = await import('../src/identity');
  if (identity.operator && identity.contact.email && identity.contact.supportServer)
    await expect(run({ PUBLIC_SITE_INDEXING: 'true' })).resolves.toBeTruthy();
  else
    await expect(run({ PUBLIC_SITE_INDEXING: 'true' })).rejects.toThrow(
      /Indexing is enabled but src\/identity.ts is missing/,
    );
});
test('four organizer destinations and theme choice preserve a community draft', async ({
  page,
}, info) => {
  await mockApp(page);
  await page.goto(`/app/?community=${guild}&view=queues&queue=${queue}`);
  await expect(page.locator('.workspace-tab')).toHaveCount(4);
  await page.getByRole('textbox', { name: 'Queue name', exact: true }).fill('Theme draft');
  await page.getByRole('button', { name: 'Account', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel('Theme').selectOption('dark');
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await expect(page.getByRole('textbox', { name: 'Queue name', exact: true })).toHaveValue(
    'Theme draft',
  );
  await expect(page.getByRole('button', { name: 'Account', exact: true })).toBeFocused();
  await page.screenshot({ path: info.outputPath('queues-dark.png'), fullPage: true });
});
test('legacy Overview links land on Queues', async ({ page }) => {
  await mockApp(page);
  await page.goto(`/?community=${guild}&view=overview`);
  await expect(page.getByRole('tab', { name: 'Queues', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  await expect(page).toHaveURL(/view=queues/);
});

test('search publishing is explicit, atomic and cleared when visibility becomes restricted', async ({
  page,
}) => {
  const state = await mockApp(page);
  await page.goto(`/app/?community=${guild}&view=setup`);
  const game = page.locator('.game-settings').first();
  await game.locator(':scope > summary').click();
  await game.getByLabel('Statistics visibility', { exact: true }).selectOption('public');
  const publishing = game.getByRole('checkbox', {
    name: 'Publish this leaderboard to search engines',
    exact: true,
  });
  await publishing.check();
  await page.locator('[data-edit="save"]').click();
  await expect(page.locator('#savebar')).toBeVisible();
  expect(state.saved).toHaveLength(0);
  await page
    .getByLabel('Public community display name', { exact: true })
    .fill('Demo public community');
  await page.locator('[data-edit="save"]').click();
  await expect.poll(() => state.saved.length).toBe(1);
  expect(state.saved[0].publicPage?.displayName).toBe('Demo public community');
  expect(state.saved[0].games[0].records?.searchPublishing).toBe(true);
  if (!(await publishing.isVisible())) await game.locator(':scope > summary').click();
  await game.getByLabel('Statistics visibility', { exact: true }).selectOption('members');
  await expect(publishing).not.toBeChecked();
  await game.getByLabel('Statistics visibility', { exact: true }).selectOption('public');
  await expect(publishing).not.toBeChecked();
});

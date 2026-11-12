import { test, expect, type Page } from '@playwright/test';
import { mockApp, guild, game, queue, user } from './fixtures';

const editor = `/app/?community=${guild}&view=queues&queue=${queue}`;
const save = (page: Page) =>
  page.getByRole('button', { name: 'Save changes', exact: true }).click();
const category = (page: Page, name: string) =>
  page.getByRole('button', { name, exact: true }).click();

test('voice, quiz and gold tax stay out of sight for a new community', async ({ page }) => {
  await mockApp(page);
  await page.goto(editor);
  await expect(page.getByRole('button', { name: 'Voice and rally', exact: true })).toHaveCount(0);
  await category(page, 'Extras');
  for (const name of ['Gold', 'Predictions', 'Civilization picks', 'Match stories'])
    await expect(page.getByRole('checkbox', { name, exact: true })).toBeVisible();
  await expect(page.getByRole('checkbox', { name: 'Quiz', exact: true })).toBeHidden();
  await expect(page.getByRole('checkbox', { name: 'Weekly gold tax', exact: true })).toBeHidden();
  await expect(page.locator('[data-category-panel="engagement"] .beta-tag')).toHaveCount(4);
  await page.getByRole('searchbox', { name: 'Search settings' }).fill('rally');
  await expect(page.locator('[data-search-empty]')).toBeVisible();
  await page.getByRole('tab', { name: 'Setup', exact: true }).click();
  await page.locator('.game-settings > summary').click();
  await expect(page.getByLabel('Weekly gold tax time (UTC)', { exact: true })).toBeHidden();
});

test('a community that already uses them can still see and turn them off', async ({ page }) => {
  const state = await mockApp(page);
  const q = state.snapshot.settings.queues[0];
  q.voicePreview = true;
  q.modules.quiz = true;
  q.modules.gold = true;
  q.economy = { taxEnabled: true, taxMinuteUtc: 600 };
  await page.goto(editor);
  await category(page, 'Voice and rally');
  const voice = page.getByRole('checkbox', { name: 'Show who is in voice', exact: true });
  await expect(voice).toBeChecked();
  await voice.uncheck();
  // Turning a feature off mid-edit keeps its control on screen so the change can be undone.
  await expect(page.locator('[data-category="voice"]')).toBeVisible();
  await category(page, 'Extras');
  await page.getByRole('checkbox', { name: 'Quiz', exact: true }).uncheck();
  await page.getByRole('checkbox', { name: 'Weekly gold tax', exact: true }).uncheck();
  await page.getByRole('tab', { name: 'Setup', exact: true }).click();
  await page.locator('.game-settings > summary').click();
  await expect(page.getByLabel('Weekly gold tax time (UTC)', { exact: true })).toHaveValue('10:00');
  await save(page);
  await expect(page.locator('#savebar')).toBeHidden();
  expect(state.saved[0].queues[0]).toMatchObject({
    voicePreview: false,
    modules: { quiz: false, gold: true },
    economy: { taxEnabled: false, taxMinuteUtc: 600 },
  });
});

test('durations are entered in units and saved as the same number of seconds', async ({ page }) => {
  const state = await mockApp(page);
  state.snapshot.settings.queues[0].controls = {
    waitingSeconds: 5400,
    matchSeconds: 43200,
    requiredRole: null,
    blockedRole: null,
  };
  await page.goto(editor);
  await category(page, 'Match preparation');
  const waiting = page.getByRole('spinbutton', { name: 'Remove waiting players after' });
  const unit = page.getByRole('combobox', { name: 'Remove waiting players after unit' });
  await expect(waiting).toHaveValue('90');
  await expect(unit).toHaveValue('60');
  await expect(waiting.locator('../..')).toContainText('= 1 h 30 min');
  await page
    .locator('[data-category-panel="preparation"] details[data-advanced] > summary')
    .click();
  await expect(page.getByRole('spinbutton', { name: 'End unreported matches after' })).toHaveValue(
    '12',
  );
  // An unrelated edit must not round a stored duration.
  await category(page, 'Basics');
  await page.getByRole('spinbutton', { name: 'Players per match', exact: true }).fill('6');
  await save(page);
  await expect(page.locator('#savebar')).toBeHidden();
  expect(state.saved[0].queues[0].controls).toMatchObject({
    waitingSeconds: 5400,
    matchSeconds: 43200,
  });
  await category(page, 'Match preparation');
  await waiting.fill('2');
  await unit.selectOption('3600');
  await expect(waiting.locator('../..')).toContainText('= 2 h');
  await page.getByRole('button', { name: 'Review changes', exact: true }).click();
  await expect(page.getByRole('dialog')).toContainText('1 h 30 min → 2 h');
  await page.getByRole('button', { name: 'Save reviewed changes', exact: true }).click();
  await expect(page.locator('#savebar')).toBeHidden();
  expect(state.saved.at(-1)!.queues[0].controls?.waitingSeconds).toBe(7200);
  await page.reload();
  await category(page, 'Match preparation');
  await expect(waiting).toHaveValue('2');
  await expect(unit).toHaveValue('3600');
  await page.getByRole('button', { name: 'Reset Remove waiting players after' }).click();
  await expect(waiting).toHaveValue('0');
  await expect(page.locator('#savebar')).toBeVisible();
});

test('settings search finds a setting inside a closed Advanced group and opens it', async ({
  page,
}) => {
  await mockApp(page);
  await page.goto(editor);
  const aliases = page.getByRole('textbox', { name: 'Queue nicknames', exact: true });
  await expect(aliases).toBeHidden();
  await page.getByRole('searchbox', { name: 'Search settings' }).fill('nicknames');
  await expect(aliases).toBeVisible();
  await aliases.fill('evening, eve');
  await page.getByRole('searchbox', { name: 'Search settings' }).fill('votes');
  await expect(
    page.getByRole('combobox', { name: 'When nobody votes', exact: true }),
  ).toBeVisible();
  await expect(page.getByRole('combobox', { name: 'When nobody votes' })).toContainText(
    'Pick a map at random',
  );
});

test('pausing a player uses a duration list and a custom length', async ({ page }) => {
  const state = await mockApp(page);
  await page.goto(`/app/?community=${guild}&view=players`);
  const card = page.locator('.player-record').first();
  await card.locator('summary').first().click();
  await card.locator('.rating-options > summary').click();
  const pause = card.locator('[data-kind="noadd"]');
  await pause.getByRole('combobox', { name: 'How long' }).selectOption('604800');
  await pause.getByRole('button', { name: 'Review pause' }).click();
  await expect(pause.locator('[data-review]')).toContainText('for 7 days');
  await pause.getByRole('button', { name: 'Confirm action' }).click();
  await expect.poll(() => state.actions.length).toBe(1);
  expect(state.actions[0]).toMatchObject({ kind: 'noadd', target: user, seconds: 604800 });
  await page.locator('#view-players .advanced-tools > summary').click();
  const tool = page.locator('#view-players .advanced-tools [data-kind="noadd"]');
  await tool.getByLabel('Player’s Discord user ID').fill('100000000000000009');
  await tool.getByRole('combobox', { name: 'How long' }).selectOption('custom');
  await tool.getByLabel('Custom length in hours').fill('3');
  await tool.getByRole('button', { name: 'Review pause' }).click();
  await tool.getByRole('button', { name: 'Confirm action' }).click();
  await expect.poll(() => state.actions.length).toBe(2);
  expect(state.actions[1]).toMatchObject({ kind: 'noadd', seconds: 10800 });
});

const forbidden = /cloudflare|legacy|nammanomad|nammapubobot|pubobot|no-add|\bnoadd\b|revision/i;
async function visibleText(page: Page) {
  await page.evaluate(() =>
    document.querySelectorAll('details').forEach((d) => ((d as HTMLDetailsElement).open = true)),
  );
  return page.locator('body').innerText();
}

test('no visible console text uses internal or community-specific words', async ({ page }) => {
  const state = await mockApp(page);
  state.matchState = 'finished';
  state.environment = 'production';
  await page.goto(`/app/?community=${guild}&view=queues`);
  await expect(page.locator('#environment')).toBeHidden();
  const seen: string[] = [];
  for (const view of ['Queues', 'Matches', 'Players', 'Setup']) {
    await page.getByRole('tab', { name: view, exact: true }).click();
    seen.push(await visibleText(page));
  }
  await page.goto(editor);
  for (const name of ['Basics', 'Access', 'Match preparation', 'Maps', 'Extras']) {
    await category(page, name);
    seen.push(await visibleText(page));
  }
  await page.goto(`/app/?community=${guild}&view=setup&guide=game&guideGame=${game}`);
  seen.push(await visibleText(page));
  for (const text of seen) {
    expect(text).not.toMatch(forbidden);
    expect(text).not.toContain('STAGING');
  }
  expect(seen.join('\n')).toContain('Lobby tracking');
});

test('non-production builds name their environment in sentence case', async ({ page }) => {
  await mockApp(page);
  await page.goto(`/app/?community=${guild}&view=matches`);
  await expect(page.locator('#environment')).toHaveText('Staging');
  await expect(page.locator('[data-matches-note]')).toContainText(
    'Results are normally reported by players in Discord',
  );
});

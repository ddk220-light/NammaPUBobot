import { test, expect } from '@playwright/test';
import { mockApp, guild, game, queue, user } from './fixtures';
const editor = `/app/?community=${guild}&view=queues&queue=${queue}`;

test('every queue category has usable help, domain defaults and correctly labelled controls', async ({
  page,
}, info) => {
  await mockApp(page);
  await page.goto(editor);
  for (const name of [
    'Basics',
    'Access',
    'Match Preparation',
    'Maps',
    'Engagement',
    'Voice / Rally',
  ]) {
    await page.getByRole('button', { name, exact: true }).click();
    const category = page.locator('[data-category-panel]:visible');
    await expect(category).toHaveCount(1);
    const help = category.getByRole('button', { name: /^More about/ }).first();
    await help.click();
    await expect(page.getByRole('dialog')).toContainText('Applies to');
    await expect(page.getByRole('dialog')).toContainText('Allowed');
    await page.keyboard.press('Escape');
    await expect(help).toBeFocused();
    await page.screenshot({ path: info.outputPath(`category-${name.replaceAll(' / ', '-')}.png`) });
  }
  await page.getByRole('button', { name: 'Basics', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Reset Queue name', exact: true })).toHaveCount(0);
  await expect(
    page.getByRole('button', { name: 'Reset Discord channel', exact: true }),
  ).toHaveCount(0);
  await expect(page.locator('#savebar')).toBeHidden();
});

test('clearing a saved map pool cannot silently erase timer or other map edits', async ({
  page,
}) => {
  const state = await mockApp(page);
  await page.goto(editor);
  await page.getByRole('button', { name: 'Maps', exact: true }).click();
  const pool = page.getByRole('textbox', { name: 'Map pool', exact: true });
  const seconds = page.getByRole('spinbutton', { name: 'Selection time (seconds)', exact: true });
  await pool.fill('');
  await seconds.fill('');
  await page.getByRole('button', { name: 'Save changes', exact: true }).click();
  expect(state.saved).toHaveLength(0);
  await expect(pool).toHaveAttribute('aria-invalid', 'true');
  await page.getByRole('tab', { name: 'Players', exact: true }).click();
  await page.getByRole('tab', { name: 'Queues', exact: true }).click();
  await expect(pool).toHaveValue('');
  await expect(seconds).toHaveValue('');
  await pool.fill('Arabia\nNomad');
  await seconds.fill('90');
  await page.getByRole('button', { name: 'Save changes', exact: true }).click();
  await expect(page.locator('#savebar')).toBeHidden();
  expect(state.saved[0].queues[0].maps).toMatchObject({ pool: ['Arabia', 'Nomad'], seconds: 90 });
});

test('custom map edits require a pool and preserve the selected options until corrected', async ({
  page,
}) => {
  const state = await mockApp(page);
  state.snapshot.settings.games[0].catalogId = null;
  delete state.snapshot.settings.queues[0].maps;
  await page.goto(editor);
  await page.getByRole('button', { name: 'Maps', exact: true }).click();
  await page.getByRole('spinbutton', { name: 'Selection time (seconds)', exact: true }).fill('120');
  await page
    .getByRole('combobox', { name: 'Selection method', exact: true })
    .selectOption('random');
  await page.getByRole('button', { name: 'Save changes', exact: true }).click();
  expect(state.saved).toHaveLength(0);
  await expect(page.getByRole('textbox', { name: 'Map pool', exact: true })).toHaveAttribute(
    'aria-invalid',
    'true',
  );
  await page.getByRole('textbox', { name: 'Map pool', exact: true }).fill('First map\nSecond map');
  await page.getByRole('button', { name: 'Save changes', exact: true }).click();
  await expect(page.locator('#savebar')).toBeHidden();
  expect(state.saved[0].queues[0].maps).toMatchObject({
    mode: 'random',
    seconds: 120,
    shortlist: 1,
  });
});

test('map shortlist constraints match server validation and a failed save keeps the draft', async ({
  page,
}) => {
  const state = await mockApp(page);
  await page.goto(editor);
  await page.getByRole('button', { name: 'Maps', exact: true }).click();
  const shortlist = page.getByRole('spinbutton', { name: 'Shortlist size', exact: true });
  await expect(shortlist).toHaveAttribute('min', '1');
  await shortlist.fill('0');
  await page.getByRole('button', { name: 'Save changes', exact: true }).click();
  await expect(shortlist).toHaveAttribute('aria-invalid', 'true');
  await expect(shortlist).toHaveValue('0');
  expect(state.saved).toHaveLength(0);
  await shortlist.fill('1');
  await page.getByRole('button', { name: 'Save changes', exact: true }).click();
  await expect(page.locator('#savebar')).toBeHidden();
});

test('predictions explain their Gold dependency without silently clearing a staged choice', async ({
  page,
}) => {
  const state = await mockApp(page);
  await page.goto(editor);
  await page.getByRole('button', { name: 'Engagement', exact: true }).click();
  const gold = page.getByRole('checkbox', { name: 'Gold', exact: true });
  const predictions = page.getByRole('checkbox', { name: 'Predictions', exact: true });
  await expect(predictions).toBeDisabled();
  await gold.check();
  await expect(predictions).toBeEnabled();
  await predictions.check();
  await gold.uncheck();
  await expect(predictions).toBeChecked();
  await expect(predictions).toBeEnabled();
  await page.getByRole('button', { name: 'Save changes', exact: true }).click();
  await expect(predictions).toHaveAttribute('aria-invalid', 'true');
  expect(state.saved).toHaveLength(0);
  await page.getByRole('tab', { name: 'Matches', exact: true }).click();
  await page.getByRole('tab', { name: 'Queues', exact: true }).click();
  await expect(predictions).toBeChecked();
  await gold.check();
  await page.getByRole('button', { name: 'Save changes', exact: true }).click();
  await expect(page.locator('#savebar')).toBeHidden();
  expect(state.saved[0].queues[0].modules).toMatchObject({ gold: true, predictions: true });
});

test('malformed settings and profile section links fall back to a visible section', async ({
  page,
}) => {
  await mockApp(page);
  for (const section of ['toString', '__proto__', 'constructor', 'not-a-category']) {
    await page.goto(editor + `&section=${section}`);
    await expect(page.getByRole('textbox', { name: 'Queue name', exact: true })).toBeVisible();
    await page.goto(
      `/stats?community=${guild}&game=${game}&user=${user}&section=${section}&page=Infinity`,
    );
    await expect(page.locator('[data-profile-panel=overview]')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Overview', exact: true })).toHaveAttribute(
      'aria-current',
      'true',
    );
  }
});

test('reverting a list Pause action in settings clears the effective draft without a save', async ({
  page,
}) => {
  const state = await mockApp(page);
  await page.goto(`/app/?community=${guild}&view=queues`);
  await page.getByRole('button', { name: 'Pause on save', exact: true }).click();
  await expect(page.locator('#savebar')).toBeVisible();
  await page.getByRole('button', { name: 'Settings for Evening games', exact: true }).click();
  await expect(page.getByRole('checkbox', { name: 'Enable queue', exact: true })).not.toBeChecked();
  await page.getByRole('checkbox', { name: 'Enable queue', exact: true }).check();
  await expect(page.locator('#savebar')).toBeHidden();
  await page.locator('[data-queue-list]').click();
  await expect(page.locator('.queue-card')).not.toContainText('Unsaved changes');
  expect(state.saved).toHaveLength(0);
});

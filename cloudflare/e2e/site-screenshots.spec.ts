import { test, expect } from '@playwright/test';
import { mockApp, guild } from './fixtures';
test('capture public and organizer screens with labelled demo records', async ({ page }, info) => {
  await mockApp(page);
  for (const [width, height] of [
    [1440, 1000],
    [390, 844],
  ]) {
    await page.setViewportSize({ width, height });
    for (const view of ['queues', 'matches', 'players', 'setup']) {
      await page.goto(`/app/?community=${guild}&view=${view}`);
      await expect(
        page.getByRole('tab', { name: view[0].toUpperCase() + view.slice(1), exact: true }),
      ).toHaveAttribute('aria-selected', 'true');
      await page.screenshot({ path: info.outputPath(`${view}-${width}.png`), fullPage: true });
    }
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  for (const [path, name] of [
    ['/', 'home'],
    ['/games/age-of-empires-2/', 'game'],
    ['/games/age-of-empires-2/setup/', 'guide'],
    ['/features/', 'features'],
    ['/about/', 'about'],
    ['/open-source/', 'source'],
  ]) {
    await page.goto(path);
    await expect(page.locator('h1')).toHaveCount(1);
    await page.screenshot({ path: info.outputPath(`${name}.png`), fullPage: true });
  }
});

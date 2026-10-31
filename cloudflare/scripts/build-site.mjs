import { mkdir, readFile, writeFile, copyFile, rm } from 'node:fs/promises';
import { build } from 'esbuild';
import { pathToFileURL } from 'node:url';
import { games, articles, landing, setup } from '../site/content.mjs';
const bundle = 'dist/site-render.mjs';
await build({
  stdin: {
    contents:
      "export * from './src/public-site'; export {selectableCatalog} from './src/games/catalog';",
    resolveDir: process.cwd(),
  },
  bundle: true,
  format: 'esm',
  platform: 'node',
  outfile: bundle,
});
const {
  page,
  htmlEscape: e,
  selectableCatalog,
  identity,
} = await import(pathToFileURL(`${process.cwd()}/${bundle}`));
const origin = process.env.PUBLIC_SITE_ORIGIN || 'https://example.invalid';
const published =
  process.env.PUBLIC_SITE_INDEXING === 'true' && origin !== 'https://example.invalid';
for (const game of selectableCatalog) {
  const content = games.find((g) => g[0] === game.id);
  if (!content) throw Error(`Missing authored content for ${game.id}`);
  content[2] = game.name;
  content[4] = game.selection;
}
if (games.some((g) => !selectableCatalog.some((c) => c.id === g[0])))
  throw Error('Public game content must use selectable catalog entries');
const pages = [];
const out = 'dist/web';
await mkdir(`${out}/app`, { recursive: true });
await writeFile(
  `${out}/app/index.html`,
  (await readFile(`${out}/index.html`, 'utf8')).replaceAll(
    'Community Games',
    e(identity.publicName),
  ),
);
await copyFile(`${out}/app/index.html`, `${out}/app-shell.txt`);
async function emit(path, title, description, body) {
  await mkdir(`${out}${path}`, { recursive: true });
  await writeFile(
    `${out}${path}index.html`,
    page(
      title.replaceAll('Community Games', identity.publicName),
      description.replaceAll('Community Games', identity.publicName),
      body.replaceAll('Community Games', e(identity.publicName)),
      origin + path,
      published,
    ),
  );
  pages.push(path);
}
const cards = (items) =>
  `<div class="cards">${items.map(([url, name, desc]) => `<article><h2><a href="${url}">${e(name)}</a></h2><p>${e(desc)}</p></article>`).join('')}</div>`;
const gameGrid = cards(
  games.map((g) => [`/games/${g[1]}/`, g[2], `${g[3]} · queues, teams and community ratings`]),
);
const discordPreview = `<figure class="discord-example"><figcaption># pickups · an illustrative Discord queue</figcaption><div class="discord-app-name">Community Games <span>App</span></div><div class="discord-message"><div class="discord-message-heading"><strong>4v4 team games</strong><span>6 / 8</span></div><div class="discord-roster">${[
  ['Alex', '1450'],
  ['Rin', '1380'],
  ['Sam', '1325'],
  ['Jo', '1290'],
  ['Kai', '1210'],
  ['Dev', '1185'],
]
  .map(([name, rating]) => `<div><span>${name}</span><span>${rating}</span></div>`)
  .join(
    '',
  )}</div><div class="discord-buttons" aria-hidden="true"><span>Join</span><span>Leave</span></div></div><div class="discord-ready"><strong>Teams are ready</strong><p>Check in, play together, and confirm the result.</p></div><p class="preview-caption">Example records · your community sets the rules.</p></figure>`;
await emit(
  '/',
  'Free, open-source Discord tools for your gaming community',
  'Organize pickup queues, form teams, manage community ratings and review matches in a web dashboard.',
  `<section class="site-hero" data-site-hero><div class="hero-copy"><h1>Free, open-source Discord tools for your gaming community</h1><p class="lead">Players join with <code>/add</code>. Check everyone in, form teams, and keep your community’s ratings and match history in one place.</p><div class="hero-actions"><a class="button" href="/app/">Get started</a><a class="button secondary" href="/guides/getting-started/">How setup works</a></div><p class="hero-note">Community-built. GPL-3.0-only. <a href="/open-source/">Inspect the code or run your own copy.</a></p></div>${discordPreview}</section><section class="site-section games-section"><div class="site-section-heading"><div><h2>Games your community plays</h2><p>Pick a game. Make it your community’s game night.</p></div><a href="/games/">All games →</a></div>${gameGrid}<p class="section-note">Custom games work with manual reporting. Optional connections are Beta and show their actual availability in Setup.</p></section><section class="site-section" id="how"><div class="site-section-heading"><div><h2>From waiting around to playing together</h2><p>A clear routine for your next pickup game.</p></div></div><ol class="workflow-grid"><li><span>1</span><h3>Gather your players</h3><p>Join a named queue in Discord. Everyone knows where to go.</p></li><li><span>2</span><h3>Check in and form teams</h3><p>Confirm who is ready, then use captains, balanced or random teams.</p></li><li><span>3</span><h3>Play and confirm</h3><p>Captains confirm the result. Community records stay understandable.</p></li><li><span>4</span><h3>Keep the story</h3><p>Review ratings, results and recent history in the dashboard.</p></li></ol></section><section class="site-section feature-section"><div class="site-section-heading"><div><h2>Your community, your rules</h2><p>Useful controls, without a complicated game night.</p></div></div>${cards(
    [
      [
        '/features/queues-and-teams/',
        'Queues that fit your group',
        'Set player counts, check-in and team choices for each queue.',
      ],
      [
        '/features/ratings-and-leaderboards/',
        'Ratings with context',
        'Shared game policy, clear visibility and optional public leaderboards.',
      ],
      [
        '/features/web-dashboard/',
        'One organizer dashboard',
        'Draft settings safely and manage matches, players and imports.',
      ],
    ],
  )}</section><section class="site-section source-section"><div><h2>Open source is part of the point</h2><p>Understand how the bot works. Suggest a change. Run your own installation. The code is public under GPL-3.0-only, with credit to PUBobot2 and the community projects it builds on.</p></div><div class="hero-actions"><a class="button" href="/open-source/">About the source</a><a class="button secondary" href="https://github.com/ddk220-light/NammaPUBobot">View on GitHub ↗</a></div></section><section class="site-section dashboard-section"><div class="site-section-heading"><div><h2>A dashboard that stays out of the way</h2><p>Queues, Matches, Players and Setup. The same tools on desktop and phone.</p></div><a href="/features/web-dashboard/">Explore the dashboard →</a></div><figure><picture><source media="(max-width:640px)" srcset="/screenshots/organizer-demo-phone.png" width="390" height="844"><img src="/screenshots/organizer-demo.png" width="1440" height="1000" alt="Organizer queue dashboard using illustrative demo records" loading="lazy"></picture><figcaption>Illustrative dashboard using demo records.</figcaption></figure></section>`,
);
await emit(
  '/games/',
  'Discord bots for community game nights',
  'Choose your game and follow a practical setup guide.',
  cards(
    games.map((g) => [
      `/games/${g[1]}/`,
      g[2],
      `Supported scope: ${g[3]}. Optional connections are Beta; availability varies.`,
    ]),
  ),
);
for (const g of games) {
  const body = landing(g);
  const words = body
    .replace(/<[^>]*>/g, ' ')
    .split(/\s+/)
    .filter(Boolean).length;
  if (words < 700 || words > 1200) throw Error(`${g[2]} needs 700–1200 words, got ${words}`);
  await emit(
    `/games/${g[1]}/`,
    `${g[2]} Discord bot`,
    `Free, open-source pickup queues, teams and community ratings for ${g[2]}.`,
    body,
  );
  await emit(
    `/games/${g[1]}/setup/`,
    `Set up ${g[2]} on Discord`,
    `Set up ${g[2]}: a practical guide from invitation to your first saved queue.`,
    setup(g),
  );
}
for (const [path, [title, desc, body]] of Object.entries(articles))
  await emit(path, title, desc, body);
for (const [path, title, prefix] of [
  ['/features/', 'Features', '/features/'],
  ['/guides/', 'Setup and operator guides', '/guides/'],
])
  await emit(
    path,
    title,
    `Explore ${title.toLowerCase()} and configure your community.`,
    cards(
      Object.entries(articles)
        .filter(([p]) => p.startsWith(prefix))
        .map(([p, [t, d]]) => [p, t, d]),
    ),
  );
const { commands } = await import('../src/discord/commands.mjs');
function command(c, prefix = '') {
  const name = prefix ? `${prefix} ${c.name}` : `/${c.name}`;
  return `<li><code>${e(name)}</code>: ${e(c.description)}${c.options?.length ? `<ul>${c.options.map((o) => command(o, name)).join('')}</ul>` : ''}</li>`;
}
await emit(
  '/guides/commands/',
  'Discord command reference',
  'Registered commands, options and subcommands.',
  `<ul>${commands.map((c) => command(c)).join('')}</ul>`,
);
await writeFile(
  `${out}/404.html`,
  page(
    'Page not found',
    'Choose a game or return to the dashboard.',
    '<p><a href="/games/">Games</a> · <a href="/app/">Dashboard</a></p>',
    origin + '/404.html',
  ),
);
await writeFile(
  `${out}/sitemap-pages.xml`,
  `<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">${published ? pages.map((p) => `<url><loc>${e(origin + p)}</loc></url>`).join('') : ''}</urlset>`,
);
await writeFile(`${out}/site-manifest.json`, JSON.stringify({ origin, published, pages }));
await writeFile(
  `${out}/robots.txt`,
  `User-agent: *\n${published ? 'Allow: /' : '# Preview: responses carry noindex\nAllow: /'}\nSitemap: ${origin}/sitemap.xml\n`,
);
await rm(bundle);
console.log(
  `Generated ${pages.length} public pages; indexing ${published ? 'enabled' : 'disabled'}.`,
);

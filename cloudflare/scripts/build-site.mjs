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
await emit(
  '/',
  'Free, open-source Discord tools for your gaming community',
  'Organize pickup queues, form teams, manage community ratings and review matches in a web dashboard.',
  `<p>Make community game nights easier to organize with transparent tools for queues, teams, ratings and match history.</p><p><a class="button" href="/app/">Get started</a> <a href="/open-source/">Read the source and license</a></p><h2>Games your community plays</h2>${cards(games.map((g) => [`/games/${g[1]}/`, g[2], `${g[3]} · community queues and captain-confirmed results`]))}<figure><picture><source media="(max-width:640px)" srcset="/screenshots/organizer-demo-phone.png"><img src="/screenshots/organizer-demo.png" width="1440" height="1000" alt="Organizer queue dashboard using illustrative demo records" loading="lazy"></picture><figcaption>Illustrative dashboard using demo records.</figcaption></figure><h2>A game night in four steps</h2><ol><li>Join a queue in Discord with /add.</li><li>Check in and form teams.</li><li>Play and confirm the result.</li><li>Review community records.</li></ol><h2>Control your community</h2><p>Settings stay in a draft until saved. Review changes together, resolve errors without losing edits and choose who can see statistics.</p><p><a href="/features/">Explore the features</a> · <a href="/about/">Why this project exists</a></p>`,
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

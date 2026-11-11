import { gameCopy } from '../site/game-copy.mjs';
import { mkdir, readFile, writeFile, copyFile, rm } from 'node:fs/promises';
import { build } from 'esbuild';
import { pathToFileURL } from 'node:url';
import {
  games,
  articles,
  landing,
  home,
  moved,
  movedBody,
  features,
  indexes,
  guides,
  cards,
} from '../site/content.mjs';
import { commandReference } from '../site/commands.mjs';
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
  if (!gameCopy[game.id]) throw Error(`Missing game copy for ${game.id}`);
  // “Warcraft III (W3Champions)” names a connection, not the game players search for.
  content[2] = game.name.replace(/ \([^)]*\)$/, '');
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
// Every crumb but the last is a section index, so the trail is derived from the path.
const sections = { '/games/': 'Games', '/features/': 'Features', '/guides/': 'Guides' };
const trail = (path, heading) => {
  const section = Object.keys(sections).find((s) => path.startsWith(s) && path !== s);
  return [['Home', '/'], ...(section ? [[sections[section], section]] : []), [heading, path]];
};
async function emit(path, { title, heading = title, description, body }, extra = {}) {
  const name = identity.publicName;
  await mkdir(`${out}${path}`, { recursive: true });
  await writeFile(
    `${out}${path}index.html`,
    page(
      title.replaceAll('Community Games', name),
      description.replaceAll('Community Games', name),
      body.replaceAll('Community Games', e(name)),
      origin + (extra.canonical ?? path),
      published && !extra.canonical,
      { heading, crumbs: trail(path, heading), ...extra.options },
    ),
  );
  if (!extra.canonical) pages.push(path);
}
const gameCards = (level) =>
  cards(
    games.map((g) => [`/games/${g[1]}/`, e(g[2]), e(gameCopy[g[0]].short)]),
    level,
  );
await emit('/', {
  title: 'Free Discord bot for RTS pickup games',
  description: `${identity.publicName} is a free, open-source Discord bot for AoE2, AoE4, Warcraft III, BAR and more: pickup queues, check-in, balanced teams and Elo ratings.`,
  body: home(gameCards(3)),
});
await emit('/games/', {
  ...indexes['/games/'],
  body: `<p>Each page says what is specific to that game and what it can’t do yet. Setup is the same for all of them.</p><h2>Supported games</h2>${gameCards(3)}<p>Playing something else? Add a custom game in the console: queues, teams, ratings and captain-reported results work for any game.</p>`,
});
for (const g of games) {
  const copy = gameCopy[g[0]];
  await emit(`/games/${g[1]}/`, {
    title: copy.title,
    heading: `${g[2]} Discord bot`,
    // Line one answers the search: who we are and which game.
    description: `${identity.publicName} is a free, open-source Discord bot that runs ${copy.game} pickup games: ${copy.tail}`,
    body: landing(g),
  });
}
for (const [path, entry] of Object.entries(articles)) {
  const [title, description, body] = Array.isArray(entry) ? entry : [];
  await emit(path, Array.isArray(entry) ? { title, description, body } : entry);
}
await emit('/features/', {
  ...indexes['/features/'],
  body: `<p>${e(identity.publicName)} is free and open source, with nothing held back. Features marked beta work but may change.</p>${features.map(([group, items]) => `<h2>${group}</h2>${cards(items, 3)}`).join('')}`,
});
await emit('/guides/', {
  ...indexes['/guides/'],
  body: `<p>Start with the five-step guide; the rest are for when you need them.</p><h2>All guides</h2>${cards(guides, 3)}`,
});
// The public reference documents what strangers' servers receive.
const commands = (await import('../src/discord/commands.mjs')).commandsFor('production');
await emit('/guides/commands/', {
  title: 'Pickup bot commands for players and organizers',
  heading: 'Command reference',
  description:
    'Every slash command the pickup bot registers, grouped for players, captains and organizers, with one line on what each one does.',
  body: commandReference(commands, e),
});
for (const [from, to] of Object.entries(moved)) {
  if (!pages.includes(to)) throw Error(`Moved page ${from} points to missing ${to}`);
  const target = await readFile(`${out}${to}index.html`, 'utf8');
  const label = /<h1>(.*?)<\/h1>/.exec(target)[1];
  await emit(
    from,
    {
      title: `Moved: ${label}`,
      heading: 'This page has moved',
      description: `This page is now part of ${label}.`,
      body: movedBody(from, to, label),
    },
    { canonical: to, options: { head: `<meta http-equiv="refresh" content="0; url=${to}">` } },
  );
}
await writeFile(
  `${out}/404.html`,
  page(
    'Page not found',
    'That page doesn’t exist. Try the games, the setup guide or the console.',
    `<p><a href="/games/">Games</a> · <a href="/guides/getting-started/">Setup guide</a> · <a href="/app/">Console</a></p>`,
    origin + '/404.html',
  ),
);
await writeFile(
  `${out}/sitemap-pages.xml`,
  `<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">${published ? pages.map((p) => `<url><loc>${e(origin + p)}</loc></url>`).join('') : ''}</urlset>`,
);
await writeFile(`${out}/site-manifest.json`, JSON.stringify({ origin, published, pages, moved }));
await writeFile(
  `${out}/robots.txt`,
  `User-agent: *\n${published ? 'Allow: /' : '# Preview: responses carry noindex\nAllow: /'}\nSitemap: ${origin}/sitemap.xml\n`,
);
await rm(bundle);
console.log(
  `Generated ${pages.length} public pages and ${Object.keys(moved).length} moved-page notices; indexing ${published ? 'enabled' : 'disabled'}.`,
);

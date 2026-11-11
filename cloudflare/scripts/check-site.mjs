import { readFile, access } from 'node:fs/promises';
import { identity } from '../site/load-identity.mjs';
import { launchBlockers } from '../site/launch-guard.mjs';
import { commandsFor } from '../src/discord/commands.mjs';
const manifest = JSON.parse(await readFile('dist/web/site-manifest.json', 'utf8'));
// An indexed public launch must say who runs the service and how to reach them privately.
if (manifest.published || process.env.PUBLIC_SITE_INDEXING === 'true') {
  const missing = launchBlockers(identity);
  if (missing.length)
    throw Error(`Indexing is enabled but src/identity.ts is missing: ${missing.join(', ')}`);
}
const moved = manifest.moved ?? {};
const fail = (message) => {
  throw Error(message);
};
const exists = (file) =>
  access(`dist/web${file}`).then(
    () => true,
    () => false,
  );
const decode = (s) =>
  s.replace(
    /&(amp|lt|gt|quot|#39);/g,
    (_, c) => ({ amp: '&', lt: '<', gt: '>', quot: '"', '#39': "'" })[c],
  );
const strip = (s) => decode(s.replace(/<[^>]*>/g, ''));
// Words that describe the system rather than the game night. Legal and operator pages are exempt,
// and so is the generated command list, whose wording belongs to the command definitions.
const jargon =
  /\b(observations?|bindings?|providers?|revisions?|drafts?|configuration changes?|deployments?|Cloudflare|Durable Objects?)\b/i;
const exempt = [
  '/privacy/',
  '/terms/',
  '/about/',
  '/changelog/',
  '/guides/self-hosting/',
  '/guides/commands/',
];
// Every command a page names must be one Discord would actually offer.
const commandPaths = new Set();
const addCommand = (option, path) => {
  commandPaths.add(path);
  for (const o of option.options ?? [])
    if (o.type === 1 || o.type === 2) addCommand(o, `${path} ${o.name}`);
};
for (const c of commandsFor('staging')) addCommand(c, `/${c.name}`);
const titles = new Set(),
  descriptions = new Set();
for (const path of manifest.pages) {
  const html = await readFile(`dist/web${path}index.html`, 'utf8');
  const title = /<title>(.*?)<\/title>/.exec(html)?.[1],
    desc = /<meta name="description" content="([^"]+)"/.exec(html)?.[1];
  if (!title || titles.has(title) || !desc || descriptions.has(desc))
    fail(`Missing or duplicated metadata: ${path}`);
  titles.add(title);
  descriptions.add(desc);
  if (decode(title).length > 62) fail(`Title over 62 characters: ${path} (${decode(title)})`);
  if (decode(desc).length > 160 || decode(desc).length < 50)
    fail(`Description must be 50–160 characters: ${path} (${decode(desc).length})`);
  if ((html.match(/<h1[ >]/g) || []).length !== 1) fail(`Exactly one H1 required: ${path}`);
  const main = /<main[\s\S]*<\/main>/.exec(html)[0];
  let level = 1;
  for (const [, n] of main.matchAll(/<h([1-6])[ >]/g)) {
    if (+n > level + 1) fail(`Heading jumps from h${level} to h${n}: ${path}`);
    level = +n;
  }
  if (!html.includes(`rel="canonical" href="${manifest.origin}${path}"`))
    fail(`Incorrect canonical: ${path}`);
  const graphs = [
    ...html.matchAll(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/g),
  ].map((m) => JSON.parse(m[1]));
  if (!graphs.length || graphs.some((g) => g['@context'] !== 'https://schema.org'))
    fail(`Missing or invalid JSON-LD: ${path}`);
  const list = graphs
    .flatMap((g) => g['@graph'] ?? [g])
    .find((n) => n['@type'] === 'BreadcrumbList');
  const visible = [
    ...(/<nav class="breadcrumbs"[^>]*>([\s\S]*?)<\/nav>/.exec(html)?.[1] ?? '').matchAll(
      /<a href="([^"]+)">(.*?)<\/a>|<span aria-current="page">(.*?)<\/span>/g,
    ),
  ].map(([, href, a, current]) => [strip(a ?? current), href ?? path]);
  const structured = (list?.itemListElement ?? []).map((i) => [i.name, new URL(i.item).pathname]);
  if (JSON.stringify(visible) !== JSON.stringify(structured))
    fail(`Breadcrumb JSON-LD does not match the visible breadcrumb: ${path}`);
  if (path !== '/' && (visible.length < 2 || visible.at(-1)[1] !== path))
    fail(`Breadcrumb must end at the page itself: ${path}`);
  for (const [, href] of html.matchAll(/href="(\/[^"?#]*)/g))
    if (
      !manifest.pages.includes(href) &&
      !['/app/', '/invite', '/site.css'].includes(href) &&
      !(/\.(svg|png)$/.test(href) && (await exists(href)))
    )
      fail(`Broken public link ${path} -> ${href}${moved[href] ? ' (moved page)' : ''}`);
  for (const [img] of main.matchAll(/<img [^>]*>/g)) {
    const src = /src="([^"]+)"/.exec(img)?.[1];
    if (!src || !(await exists(src))) fail(`Missing image ${src}: ${path}`);
    if (!/ width="\d+"/.test(img) || !/ height="\d+"/.test(img) || !/ alt="/.test(img))
      fail(`Images need width, height and alt: ${path} ${src}`);
  }
  for (const [, code] of main.matchAll(/<code>(\/[a-z][^<]*)<\/code>/g)) {
    const words = decode(code).split(' ');
    let known = words[0];
    if (!commandPaths.has(known)) fail(`Unknown command ${code}: ${path}`);
    for (const word of words.slice(1))
      if (commandPaths.has(`${known} ${word}`)) known += ` ${word}`;
      else break;
    // A bare word after a command that has subcommands is a subcommand that does not exist.
    const next = words[known.split(' ').length];
    if (next && /^[a-z-]+$/.test(next) && [...commandPaths].some((p) => p.startsWith(`${known} `)))
      fail(`Unknown subcommand ${code}: ${path}`);
  }
  for (const [, id] of html.matchAll(/href="#([^"]+)"/g))
    if (!html.includes(`id="${id}"`)) fail(`Broken anchor #${id}: ${path}`);
  if (/\[org\]|\[repo\]|\[Support Discord/.test(html)) fail(`Unresolved placeholder: ${path}`);
  if (/free hosting forever|preview does not promise/i.test(html))
    fail(`Pricing hedge contradicts the free service: ${path}`);
  if (/Community Games|NammaMatchBot/.test(html)) fail(`Old product name: ${path}`);
  if (!exempt.includes(path)) {
    const word = jargon.exec(strip(main));
    if (word) fail(`Internal wording “${word[0]}” on a public page: ${path}`);
  }
}
for (const [from, to] of Object.entries(moved)) {
  const html = await readFile(`dist/web${from}index.html`, 'utf8');
  if (!manifest.pages.includes(to)) fail(`Moved page ${from} points to missing ${to}`);
  if (manifest.pages.includes(from)) fail(`Moved page ${from} is still listed as a page`);
  if (!html.includes(`rel="canonical" href="${manifest.origin}${to}"`))
    fail(`Moved page ${from} must name ${to} as canonical`);
  if (!html.includes('content="noindex"') || !html.includes(`url=${to}"`))
    fail(`Moved page ${from} must be noindex and refresh to ${to}`);
}
const sitemap = await readFile('dist/web/sitemap-pages.xml', 'utf8');
const listed = [...sitemap.matchAll(/<loc>([^<]+)<\/loc>/g)].map((m) => decode(m[1]));
const expected = manifest.published ? manifest.pages.map((p) => manifest.origin + p) : [];
if (JSON.stringify(listed) !== JSON.stringify(expected))
  fail('Sitemap must list exactly the published pages');
for (const path of ['/privacy/', '/terms/']) {
  const html = await readFile(`dist/web${path}index.html`, 'utf8');
  if (!/Last updated: <time datetime="\d{4}-\d{2}-\d{2}">/.test(html))
    fail(`Missing last-updated date: ${path}`);
  if (!/<h2[^>]*>Contact<\/h2>/.test(html)) fail(`Missing contact section: ${path}`);
}
for (const font of ['plex-sans', 'plex-sans-600', 'plex-sans-700', 'plex-mono']) {
  const bytes = await readFile(`dist/web/fonts/${font}.woff2`);
  if (bytes.subarray(0, 4).toString() !== 'wOF2') fail(`Invalid font ${font}`);
}
console.log(
  `Validated HTML, metadata, breadcrumbs, structured data, links, images and fonts for ${manifest.pages.length} pages and ${Object.keys(moved).length} moved pages.`,
);

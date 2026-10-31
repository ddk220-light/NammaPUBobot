import { readFile } from 'node:fs/promises';
const manifest = JSON.parse(await readFile('dist/web/site-manifest.json', 'utf8'));
const titles = new Set(),
  descriptions = new Set();
for (const path of manifest.pages) {
  const html = await readFile(`dist/web${path}index.html`, 'utf8');
  const title = /<title>(.*?)<\/title>/.exec(html)?.[1],
    desc = /<meta name="description" content="([^"]+)"/.exec(html)?.[1];
  if (!title || titles.has(title) || !desc || descriptions.has(desc))
    throw Error(`Missing or duplicated metadata: ${path}`);
  titles.add(title);
  descriptions.add(desc);
  if ((html.match(/<h1[ >]/g) || []).length !== 1) throw Error(`Exactly one H1 required: ${path}`);
  if (!html.includes(`rel="canonical" href="${manifest.origin}${path}"`))
    throw Error(`Incorrect canonical: ${path}`);
  for (const block of html.matchAll(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/g))
    JSON.parse(block[1]);
  for (const [, href] of html.matchAll(/href="(\/[^"?#]*)/g))
    if (
      !manifest.pages.includes(href) &&
      !['/app/', '/favicon.svg', '/icon-32.png', '/icon-180.png', '/site.css'].includes(href)
    )
      throw Error(`Broken public link ${path} -> ${href}`);
  if (/\[org\]|\[repo\]|\[Support Discord/.test(html))
    throw Error(`Unresolved placeholder: ${path}`);
}
for (const font of ['plex-sans', 'plex-sans-600', 'plex-sans-700', 'plex-mono']) {
  const bytes = await readFile(`dist/web/fonts/${font}.woff2`);
  if (bytes.subarray(0, 4).toString() !== 'wOF2') throw Error(`Invalid font ${font}`);
}
console.log(
  `Validated HTML, metadata, structured data, links and fonts for ${manifest.pages.length} pages.`,
);

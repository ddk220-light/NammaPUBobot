const origin = process.argv[2] || 'http://localhost:8787';
const routes = [
  ['/', 'text/html', 200],
  ['/app/', 'text/html', 200],
  ['/stats', 'text/html', 200],
  ['/link', 'text/html', 200],
  ['/games/age-of-empires-2/', 'text/html', 200],
  ['/robots.txt', 'text/plain', 200],
  ['/sitemap.xml', 'application/xml', 200],
  ['/missing-public-page/', 'text/html', 404],
  ['/theme.js', 'javascript', 200],
  ['/fonts/plex-sans.woff2', 'font/woff2', 200],
];
for (const [path, type, status] of routes) {
  const r = await fetch(origin + path),
    body = await r.text();
  if (r.status !== status || !r.headers.get('content-type')?.includes(type))
    throw Error(`${path}: wrong status/type`);
  if (['/app/', '/stats', '/link'].includes(path) && !body.includes('Loading your communities'))
    throw Error(`${path}: organizer entry missing`);
  if (path.startsWith('/games/') && !body.includes('captains confirm results'))
    throw Error('Public content missing');
  if (!r.headers.get('x-robots-tag')?.includes('noindex'))
    throw Error(`${path}: preview is not noindex`);
  console.log(`${r.status} ${path}`);
}

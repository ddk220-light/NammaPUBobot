import { identity } from './identity';
export { identity } from './identity';
export const htmlEscape = (v: unknown) =>
  String(v).replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!,
  );
export function page(
  title: string,
  description: string,
  body: string,
  canonical: string,
  indexable = false,
) {
  const e = htmlEscape;
  const structured = JSON.stringify({
    '@context': 'https://schema.org',
    '@graph': [
      { '@type': 'WebSite', name: identity.publicName, url: new URL('/', canonical).href },
      {
        '@type': 'SoftwareSourceCode',
        name: identity.publicName,
        codeRepository: identity.repository,
        license: identity.license,
      },
      {
        '@type': 'BreadcrumbList',
        itemListElement: [
          { '@type': 'ListItem', position: 1, name: 'Home', item: new URL('/', canonical).href },
          { '@type': 'ListItem', position: 2, name: title, item: canonical },
        ],
      },
    ],
  }).replaceAll('<', String.raw`\u003c`);
  const path = new URL(canonical).pathname;
  const home = path === '/' && body.includes('data-site-hero');
  const doc = !home && !path.startsWith('/c/');
  const headings: { id: string; label: string }[] = [];
  if (doc)
    body = body.replace(/<h2(?:[^>]*)>([\s\S]*?)<\/h2>/g, (_match, label) => {
      const id = `section-${headings.length + 1}`;
      headings.push({ id, label: label.replace(/<[^>]*>/g, '') });
      return `<h2 id="${id}">${label}</h2>`;
    });
  const mark = '<img class="brand-mark" src="/favicon.svg" width="28" height="28" alt="" />';
  const nav = [
    ['/games/', 'Games'],
    ['/features/', 'Features'],
    ['/guides/', 'Guides'],
    ['/open-source/', 'Open source'],
    ['/about/', 'About'],
  ]
    .map(
      ([url, label]) =>
        `<a href="${url}" ${path.startsWith(url) ? 'aria-current="page"' : ''}>${label}</a>`,
    )
    .join('');
  const toc =
    headings.length > 2
      ? `<nav class="article-toc" aria-label="On this page"><h2>On this page</h2>${headings.map((h) => `<a href="#${h.id}">${h.label}</a>`).join('')}</nav>`
      : '';
  const callout = `<section class="site-callout"><h2>Make your next game easier</h2><p>Set up a queue, gather your players, and keep the game night moving.</p><a class="button" href="/app/">Open dashboard</a><a class="text-link" href="/guides/getting-started/">Read the setup guide →</a></section>`;
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="robots" content="${indexable ? 'index,follow' : 'noindex'}"><title>${e(title)} | ${e(identity.publicName)}</title><meta name="description" content="${e(description)}"><link rel="canonical" href="${e(canonical)}"><meta property="og:title" content="${e(title)}"><meta property="og:description" content="${e(description)}"><meta property="og:type" content="website"><meta property="og:url" content="${e(canonical)}"><meta property="og:image" content="${e(new URL('/social.png', canonical).href)}"><meta name="twitter:card" content="summary_large_image"><script type="application/ld+json">${structured}</script><script src="/theme.js"></script><script defer src="/site-navigation.js"></script><link rel="icon" href="/favicon.svg"><link rel="icon" sizes="32x32" href="/icon-32.png"><link rel="apple-touch-icon" href="/icon-180.png"><link rel="stylesheet" href="/site.css"></head><body class="${home ? 'site-home' : doc ? 'site-document' : 'site-community'}"><a class="skip" href="#content">Skip to content</a><header class="site-header"><div class="site-header-inner"><a class="brand" href="/">${mark}<span>${e(identity.publicName)}</span></a><nav class="site-nav" aria-label="Main">${nav}</nav><div class="site-header-actions"><a class="github-link" href="${identity.repository}">GitHub ↗</a><a class="button secondary" href="/app/">Open dashboard</a></div></div></header><main id="content" class="site-main">${home ? body : `<div class="document-heading"><nav class="breadcrumbs" aria-label="Breadcrumb"><a href="/">Home</a><span aria-hidden="true">/</span><span>${e(title)}</span></nav><h1>${e(title)}</h1><p class="lead">${e(description)}</p></div><div class="${doc ? 'document-layout' : 'community-layout'}"><article class="article-body">${body}</article>${doc ? `<aside class="article-sidebar">${toc}${callout}</aside>` : ''}</div>`}</main><footer class="site-footer"><div class="footer-inner"><div class="footer-intro"><a class="brand" href="/">${mark}<span>${e(identity.publicName)}</span></a><p>${e(identity.mission)}</p><span>Free, open-source software · GPL-3.0-only</span></div><nav aria-label="Explore"><h2>Explore</h2><a href="/games/">Supported games</a><a href="/features/">Features</a><a href="/guides/">Setup guides</a><a href="/guides/commands/">Commands</a><a href="/guides/self-hosting/">Self-hosting</a></nav><nav aria-label="Project"><h2>The project</h2><a href="/about/">About</a><a href="/open-source/">Open source</a><a href="${identity.repository}">GitHub</a><a href="${identity.license}">License</a><a href="/changelog/">Changelog</a></nav><nav aria-label="Support"><h2>Help and information</h2><a href="/guides/discord-permissions/">Discord permissions</a><a href="${identity.repository}/issues">Contact and issues</a><a href="/privacy/">Privacy</a><a href="/terms/">Terms</a></nav></div></footer></body></html>`;
}

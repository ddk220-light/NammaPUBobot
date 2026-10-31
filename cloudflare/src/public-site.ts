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
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="robots" content="${indexable ? 'index,follow' : 'noindex'}"><title>${e(title)} | ${e(identity.publicName)}</title><meta name="description" content="${e(description)}"><link rel="canonical" href="${e(canonical)}"><meta property="og:title" content="${e(title)}"><meta property="og:description" content="${e(description)}"><meta property="og:type" content="website"><meta property="og:url" content="${e(canonical)}"><meta property="og:image" content="${e(new URL('/social.png', canonical).href)}"><meta name="twitter:card" content="summary_large_image"><script type="application/ld+json">${structured}</script><script defer src="/site-navigation.js"></script><link rel="icon" href="/favicon.svg"><link rel="icon" sizes="32x32" href="/icon-32.png"><link rel="apple-touch-icon" href="/icon-180.png"><link rel="stylesheet" href="/site.css"></head><body><a class="skip" href="#content">Skip to content</a><header><a class="brand" href="/">${e(identity.publicName)}</a><nav aria-label="Main"><a href="/games/">Games</a><a href="/features/">Features</a><a href="/guides/">Guides</a><a href="/about/">About</a><a href="${identity.repository}">GitHub</a><a class="button" href="/app/">Open dashboard</a></nav></header><main id="content"><nav aria-label="Breadcrumb"><a href="/">Home</a> / ${e(title)}</nav><h1>${e(title)}</h1><p class="lead">${e(description)}</p>${body}</main><footer><p>${e(identity.publicName)} · Free, open-source software · GPL-3.0-only</p><nav aria-label="Footer"><a href="/open-source/">Open source</a><a href="${identity.repository}">GitHub</a><a href="${identity.license}">License</a><a href="/about/">About</a><a href="/privacy/">Privacy</a><a href="/terms/">Terms</a><a href="/changelog/">Changelog</a><a href="${identity.repository}/issues">Contact and issues</a></nav></footer></body></html>`;
}

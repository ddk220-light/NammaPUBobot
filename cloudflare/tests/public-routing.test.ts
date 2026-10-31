import { describe, it, expect } from 'vitest';
import { env } from 'cloudflare:workers';
import { publicPages } from '../src/routes/public-pages';
import { page } from '../src/public-site';
const request = (path: string) => new Request('https://setup.example' + path);
describe('public routing boundaries', () => {
  it('redirects legacy organizer links without losing selection', async () => {
    const r = await publicPages(request('/?community=706320010368516198&view=overview'), env);
    expect(r.status).toBe(302);
    expect(r.headers.get('Location')).toContain('/app/?community=706320010368516198&view=overview');
  });
  it('serves real robots and sitemaps with noindex in staging', async () => {
    for (const [path, type] of [
      ['/robots.txt', 'text/plain'],
      ['/sitemap.xml', 'application/xml'],
      ['/sitemap-leaderboards.xml', 'application/xml'],
    ]) {
      const r = await publicPages(request(path), env);
      expect(r.status).toBe(200);
      expect(r.headers.get('Content-Type')).toContain(type);
      expect(r.headers.get('X-Robots-Tag')).toBe('noindex');
      expect(await r.text()).not.toContain('706320010368516198');
    }
  });
  it('returns 404 for unpublished community pages and manifest internals', async () => {
    expect((await publicPages(request('/c/unknown/game/'), env)).status).toBe(404);
    expect((await publicPages(request('/site-manifest.json'), env)).status).toBe(404);
  });
  it('keeps unknown asset routes as 404 and supplies CSP hashes for generated data', async () => {
    const fake = {
      ...env,
      ASSETS: {
        fetch: async () =>
          new Response(
            page(
              'Missing page',
              'Try another route',
              '<p>Missing</p>',
              'https://example.invalid/missing',
            ),
            { status: 404, headers: { 'Content-Type': 'text/html' } },
          ),
      } as unknown as Fetcher,
    };
    const r = await publicPages(request('/missing'), fake);
    expect(r.status).toBe(404);
    expect(r.headers.get('Content-Security-Policy')).toContain("'sha256-");
    const html = await r.text();
    expect(html).toContain('https://setup.example/missing');
    expect(html).not.toContain('https://example.invalid');
  });
  it('redirects approved production pages to the canonical origin and leaves preview sitemaps empty', async () => {
    const production = {
      ...env,
      APP_ENV: 'production' as const,
      PUBLIC_SITE_INDEXING: 'true',
      APP_ORIGIN: 'https://public.example',
    };
    const redirect = await publicPages(request('/games/?source=guide'), production);
    expect(redirect.status).toBe(308);
    expect(redirect.headers.get('Location')).toBe('https://public.example/games/?source=guide');
    const sitemap = await publicPages(request('/sitemap-pages.xml'), env);
    expect(await sitemap.text()).not.toContain('<url>');
  });
  it('escapes public metadata and keeps source identity factual', () => {
    const html = page(
      '<script>alert(1)</script>',
      'A "description"',
      '<p>Reviewed body</p>',
      'https://setup.example/',
    );
    expect(html).not.toContain('<h1><script>');
    expect(html).toContain('NammaPUBobot');
    expect(html).toContain('noindex');
  });
});

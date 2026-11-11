import { describe, it, expect } from 'vitest';
import { env } from 'cloudflare:workers';
import { publicPages } from '../src/routes/public-pages';
import { page } from '../src/public-site';
import { identity } from '../src/identity';
import { commandReference, groups } from '../site/commands.mjs';
import { commandsFor } from '../src/discord/commands.mjs';

const e = (v: unknown) => String(v).replace(/[&<>"]/g, (c) => `&#${c.charCodeAt(0)};`);
const ld = (html: string) =>
  [...html.matchAll(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/g)].flatMap(
    (m) => JSON.parse(m[1])['@graph'],
  );
const crumbs = (html: string) =>
  ld(html)
    .find((n) => n['@type'] === 'BreadcrumbList')
    ?.itemListElement.map((i: { name: string; item: string }) => [
      i.name,
      new URL(i.item).pathname,
    ]);
const visible = (html: string) =>
  [
    ...(/<nav class="breadcrumbs"[^>]*>([\s\S]*?)<\/nav>/.exec(html)?.[1] ?? '').matchAll(
      /<a href="([^"]+)">(.*?)<\/a>|<span aria-current="page">(.*?)<\/span>/g,
    ),
  ].map(([, href, a, current]) => [(a ?? current).replaceAll('&amp;', '&'), href]);
const asset = async (path: string) =>
  (await env.ASSETS.fetch(`https://setup.example${path}`)).text();

describe('public page template', () => {
  it('builds the visible breadcrumb and its JSON-LD from one trail', () => {
    const html = page(
      'AoE2 Discord bot',
      'A description long enough.',
      '<p>x</p>',
      'https://setup.example/games/age-of-empires-2/',
      false,
      {
        heading: 'Age of Empires II Discord bot',
        crumbs: [
          ['Home', '/'],
          ['Games', '/games/'],
          ['Age of Empires II Discord bot', '/games/age-of-empires-2/'],
        ],
      },
    );
    expect(crumbs(html)).toEqual([
      ['Home', '/'],
      ['Games', '/games/'],
      ['Age of Empires II Discord bot', '/games/age-of-empires-2/'],
    ]);
    expect(visible(html).map(([n]) => n)).toEqual(crumbs(html).map(([n]: string[]) => n));
    expect(html).toContain('<h1>Age of Empires II Discord bot</h1>');
    expect(html).toContain(`<title>AoE2 Discord bot | ${identity.publicName}</title>`);
  });
  it('gives the home page no breadcrumb and never repeats the product name in a title', () => {
    const html = page(
      `About ${identity.publicName}`,
      'd',
      '<div data-site-hero></div>',
      'https://setup.example/',
    );
    expect(crumbs(html)).toBeUndefined();
    expect(html).toContain(`<title>About ${identity.publicName}</title>`);
  });
  it('links the console from the header on every page, including phones', () => {
    const html = page('T', 'd', '<p>x</p>', 'https://setup.example/features/');
    expect(html).toMatch(/class="button secondary dashboard-link" href="\/app\/">Console</);
    expect(html).toContain('href="/invite">Add to Discord');
  });
});

describe('command reference', () => {
  it('lists every registered command once, grouped for players, captains and organizers', () => {
    const commands = commandsFor('production');
    const html = commandReference(commands, e);
    const leaves = (
      c: { name: string; type?: number; options?: unknown[] },
      prefix: string,
    ): string[] => {
      const subs = (c.options ?? []).filter((o: any) => o.type === 1 || o.type === 2) as any[];
      return subs.length ? subs.flatMap((o) => leaves(o, `${prefix} ${o.name}`)) : [prefix];
    };
    for (const path of commands.flatMap((c) => leaves(c, `/${c.name}`)))
      expect(html.split(`<dt><code>${path}`).length - 1, path).toBe(1);
    expect([...html.matchAll(/<h2>(.*?)<\/h2>/g)].map((m) => m[1])).toEqual(groups.map(([g]) => g));
    expect(html.indexOf('/add')).toBeLessThan(html.indexOf('<h2>Captains'));
    expect(html.indexOf('/report')).toBeGreaterThan(html.indexOf('<h2>Captains'));
    expect(html.indexOf('/admin')).toBeGreaterThan(html.indexOf('<h2>Organizers'));
  });
  it('places commands it has never seen instead of dropping them', () => {
    const html = commandReference(
      [
        { name: 'brandnew', description: 'Something new', type: 1 },
        { name: 'cap', description: 'Captain: do a thing', type: 1 },
        { name: 'boss', description: 'Do admin things', type: 1, default_member_permissions: '32' },
      ],
      e,
    );
    const at = (s: string) => html.indexOf(s);
    expect(at('/brandnew')).toBeLessThan(at('<h2>Captains'));
    expect(at('/cap')).toBeGreaterThan(at('<h2>Captains'));
    expect(at('/boss')).toBeGreaterThan(at('<h2>Organizers'));
  });
});

describe('generated public site', () => {
  it('has one h1, unique metadata, valid JSON-LD and resolving links on every page', async () => {
    const manifest = JSON.parse(await asset('/site-manifest.json'));
    expect(manifest.pages).toContain('/guides/getting-started/');
    expect(manifest.pages.filter((p: string) => p.endsWith('/setup/'))).toEqual([]);
    const titles = new Set(),
      descriptions = new Set();
    for (const path of manifest.pages) {
      const html = await asset(path);
      expect(html.match(/<h1[ >]/g), path).toHaveLength(1);
      const title = /<title>(.*?)<\/title>/.exec(html)![1];
      const desc = /<meta name="description" content="([^"]+)"/.exec(html)![1];
      expect(titles.has(title) || descriptions.has(desc), path).toBe(false);
      titles.add(title);
      descriptions.add(desc);
      expect(ld(html).length, path).toBeGreaterThan(0);
      if (path !== '/')
        expect(
          visible(html).map(([n]) => n),
          path,
        ).toEqual(crumbs(html).map(([n]: string[]) => n));
      for (const [, href] of html.matchAll(/href="(\/[^"?#]*)/g))
        expect(
          manifest.pages.includes(href) ||
            ['/app/', '/invite', '/site.css'].includes(href) ||
            /\.(png|svg)$/.test(href),
          `${path} -> ${href}`,
        ).toBe(true);
    }
  });
  it('keeps every folded URL alive with a canonical link to its new home', async () => {
    const manifest = JSON.parse(await asset('/site-manifest.json'));
    expect(Object.keys(manifest.moved)).toContain('/games/age-of-empires-2/setup/');
    for (const [from, to] of Object.entries(manifest.moved) as [string, string][]) {
      const r = await publicPages(new Request(`https://setup.example${from}`), env);
      expect(r.status, from).toBe(200);
      const html = await r.text();
      expect(html).toContain(`rel="canonical" href="https://setup.example${to}"`);
      expect(html).toContain('content="noindex"');
      expect(html).toContain(`href="${to}"`);
    }
  });
});

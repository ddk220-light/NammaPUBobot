import { env, exports } from 'cloudflare:workers';
import { createExecutionContext, reset, runInDurableObject } from 'cloudflare:test';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { parse } from 'jsonc-parser';
import wranglerSource from '../wrangler.jsonc?raw';
import app from '../src/worker';
import { publicPages } from '../src/routes/public-pages';
import { emptyModules, type Settings } from '../src/domain/config';
import type { Env } from '../src/env';

const guild = '706320010368516198',
  unknown = '706320010368519999',
  channel = '706320010368516200',
  game = '4daebc0a-55e8-4d56-a17d-d94913644910',
  userId = '100000000000000002';
const sessionId = 'a'.repeat(64),
  csrf = 'b'.repeat(64),
  base = 'https://setup.example';
const settings: Settings = {
  games: [{ id: game, name: 'Any RTS', catalogId: null }],
  queues: [
    {
      id: '81da571b-6a4a-4e5d-8e32-f4050df35a2d',
      name: 'Pickup',
      channelId: channel,
      gameId: game,
      size: 4,
      enabled: true,
      checkIn: { enabled: true, seconds: 60 },
      teamMethod: 'balanced',
      ratingEngine: 'aoe2',
      modules: { ...emptyModules },
    },
  ],
};
let guildCalls = 0,
  guildStatus = 200,
  admin = true;
const headers = (extra: Record<string, string> = {}) => ({
  Cookie: `__Host-cg-session=${sessionId}`,
  Origin: base,
  'X-CSRF-Token': csrf,
  'Content-Type': 'application/json',
  ...extra,
});
const http = (path: string, init: RequestInit = {}) =>
  exports.default.fetch(`${base}${path}`, { redirect: 'manual', ...init });
const signIn = () =>
  env.AUTH.getByName(`session:${sessionId}`).saveSession({
    user: { id: userId, username: 'organizer', avatar: null },
    accessToken: 'test-access-token',
    csrf,
    expiresAt: Date.now() + 60000,
  });
/** What a community object has stored: tables, rows in `community`, and SQLite size. */
const stored = (g: string) =>
  runInDurableObject(env.COMMUNITIES.getByName(g), (_i, state) => {
    const sql = state.storage.sql,
      tables = sql
        .exec<{ name: string }>(`SELECT name FROM sqlite_master WHERE type='table'`)
        .toArray()
        .map((r) => r.name);
    return {
      tables: tables.length,
      community: tables.includes('community')
        ? sql.exec('SELECT * FROM community').toArray().length
        : 0,
    };
  });

beforeEach(() => {
  guildCalls = 0;
  guildStatus = 200;
  admin = true;
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = new URL(new Request(input, init).url);
    if (url.pathname === '/api/v10/users/@me/guilds') {
      guildCalls++;
      if (guildStatus !== 200) return Response.json({}, { status: guildStatus });
      return Response.json([
        { id: guild, name: 'Friends', permissions: admin ? '32' : '0', owner: false, extra: 1 },
      ]);
    }
    if (url.pathname.endsWith('/channels'))
      return Response.json([
        { id: channel, guild_id: guild, name: 'pickup', type: 0, permission_overwrites: [] },
      ]);
    if (url.pathname.endsWith('/roles'))
      return Response.json([{ id: guild, name: '@everyone', permissions: '84992' }]);
    if (url.pathname.endsWith('/members/100000000000000001')) return Response.json({ roles: [] });
    return Response.json({ id: '100000000001000000' });
  });
});
afterEach(async () => {
  await reset();
  vi.restoreAllMocks();
});

describe('unauthenticated reads do not create community storage', () => {
  it('answers statistics for an unknown guild with 404 and keeps nothing', async () => {
    const response = await http(`/statistics/${unknown}/${game}`);
    expect(response.status).toBe(404);
    expect(((await response.json()) as { code: string }).code).toBe('unknown_game');
    expect(await stored(unknown)).toEqual({ tables: 0, community: 0 });
    // Repeated probes of the same (still resident) object stay empty too.
    expect((await http(`/statistics/${unknown}/${game}`)).status).toBe(404);
    expect(await stored(unknown)).toEqual({ tables: 0, community: 0 });
  });
  it('keeps first-time setup and Discord-driven creation working after a probe', async () => {
    expect((await http(`/statistics/${guild}/${game}`)).status).toBe(404);
    expect(await stored(guild)).toEqual({ tables: 0, community: 0 });
    // The interaction path still uses the creating read on the same resident instance.
    const snapshot = await env.COMMUNITIES.getByName(guild).read(guild);
    expect(snapshot).toMatchObject({ communityId: guild, revision: 0 });
    expect((await stored(guild)).community).toBe(1);
    expect(await env.COMMUNITIES.getByName(guild).find(guild)).toEqual(snapshot);
    await signIn();
    const saved = await http(`/api/communities/${guild}/config`, {
      method: 'PUT',
      headers: headers(),
      body: JSON.stringify({ expectedRevision: 0, settings }),
    });
    expect(saved.status).toBe(200);
    const stats = await http(`/statistics/${guild}/${game}`, { headers: headers() });
    expect(stats.status).toBe(200);
  });
  it('does not keep an object that holds anything besides the empty schema', async () => {
    const s = env.COMMUNITIES.getByName(unknown);
    await runInDurableObject(s, (_i, state) => state.storage.kv.put('testMode', true));
    expect(await s.find(unknown)).toBeNull();
    expect((await stored(unknown)).tables).toBeGreaterThan(0);
  });
  it('serves public leaderboards and sitemaps from stale directory entries without creating', async () => {
    await env.PUBLIC_DIRECTORY!.getByName('directory').register({
      guild: unknown,
      revision: 1,
      community: 'gone-community',
      games: [{ id: game, slug: 'any-rts' }],
    });
    const page = await publicPages(new Request(`${base}/c/gone-community/any-rts/`), env);
    expect(page.status).toBe(404);
    const launched = { ...env, APP_ENV: 'production' as const, PUBLIC_SITE_INDEXING: 'true' };
    const sitemap = await publicPages(new Request(`${base}/sitemap-leaderboards.xml`), launched);
    expect(sitemap.status).toBe(200);
    expect(await sitemap.text()).not.toContain('gone-community');
    expect(await stored(unknown)).toEqual({ tables: 0, community: 0 });
  });
});

describe('rate limits', () => {
  const limiter = (allow: (key: string) => boolean | Promise<boolean>) => {
    const keys: string[] = [];
    return {
      keys,
      binding: {
        limit: async ({ key }: { key: string }) => {
          keys.push(key);
          return { success: await allow(key) };
        },
      } as RateLimit,
    };
  };
  const ip = { 'CF-Connecting-IP': '203.0.113.9' };
  const request = (path: string, init: RequestInit, bindings: Partial<Env>) =>
    app.request(
      `${base}${path}`,
      { redirect: 'manual', ...init },
      { ...env, ...bindings },
      createExecutionContext(),
    );

  it('limits sign-in and invitations per client address with a plain page', async () => {
    const auth = limiter(() => false);
    for (const path of ['/auth/login', '/auth/callback?state=x', '/invite']) {
      const response = await request(path, { headers: ip }, { AUTH_RATE_LIMITER: auth.binding });
      expect(response.status).toBe(429);
      expect(response.headers.get('Retry-After')).toBe('60');
      expect(response.headers.get('Content-Type')).toContain('text/plain');
      expect(await response.text()).toContain('Too many requests');
    }
    expect(auth.keys).toEqual(Array(3).fill('203.0.113.9'));
  });
  it('limits public reads with the JSON problem format for statistics', async () => {
    const pub = limiter(() => false);
    const stats = await request(
      `/statistics/${guild}/${game}`,
      { headers: ip },
      { PUBLIC_RATE_LIMITER: pub.binding },
    );
    expect(stats.status).toBe(429);
    expect(stats.headers.get('Retry-After')).toBe('60');
    expect(await stats.json()).toMatchObject({ code: 'rate_limited', error: expect.any(String) });
    for (const path of ['/c/some/game/', '/sitemap.xml', '/sitemap-leaderboards.xml'])
      expect(
        (await request(path, { headers: ip }, { PUBLIC_RATE_LIMITER: pub.binding })).status,
      ).toBe(429);
    // Static pages and assets are not counted.
    expect(
      (await request('/robots.txt', { headers: ip }, { PUBLIC_RATE_LIMITER: pub.binding })).status,
    ).toBe(200);
    expect(pub.keys).toHaveLength(4);
  });
  it('never limits Discord interactions or health checks', async () => {
    const all = limiter(() => false);
    const bindings = Object.fromEntries(
      [
        'AUTH_RATE_LIMITER',
        'PUBLIC_RATE_LIMITER',
        'API_RATE_LIMITER',
        'API_WRITE_RATE_LIMITER',
        'IMPORT_RATE_LIMITER',
      ].map((name) => [name, all.binding]),
    );
    expect((await request('/health', { headers: ip }, bindings)).status).toBe(200);
    expect(
      (await request('/interactions', { method: 'POST', headers: ip, body: '{}' }, bindings))
        .status,
    ).toBe(401);
    expect(all.keys).toEqual([]);
  });
  it('keys the console API by user and adds write and import tiers', async () => {
    await signIn();
    const api = limiter(() => true),
      write = limiter(() => true),
      imports = limiter(() => false);
    const bindings = {
      API_RATE_LIMITER: api.binding,
      API_WRITE_RATE_LIMITER: write.binding,
      IMPORT_RATE_LIMITER: imports.binding,
    };
    expect((await request('/api/communities', { headers: headers(ip) }, bindings)).status).toBe(
      200,
    );
    const imported = await request(
      `/api/communities/${guild}/import/preview`,
      { method: 'POST', headers: headers(ip), body: '{}' },
      bindings,
    );
    expect(imported.status).toBe(429);
    expect(await imported.json()).toMatchObject({ code: 'rate_limited' });
    expect(api.keys).toEqual([`user:${userId}`, `user:${userId}`]);
    expect(write.keys).toEqual([`user:${userId}`]);
    expect(imports.keys).toEqual([`user:${userId}`]);
    // Anonymous API calls fall back to the client address.
    expect((await request('/api/bootstrap', { headers: ip }, bindings)).status).toBe(200);
    expect(api.keys.at(-1)).toBe('203.0.113.9');
  });
  it('skips limits when the binding is missing or failing', async () => {
    const failing = {
      limit: async () => {
        throw new Error('limiter down');
      },
    } as RateLimit;
    vi.spyOn(console, 'error').mockImplementation(() => {});
    expect((await request('/invite', { headers: ip }, { AUTH_RATE_LIMITER: failing })).status).toBe(
      302,
    );
    expect(
      (await request('/invite', { headers: ip }, { AUTH_RATE_LIMITER: undefined })).status,
    ).toBe(302);
  });
  it('uses the configured binding locally and blocks after the auth budget', async () => {
    let last = 0;
    for (let i = 0; i < 31; i++)
      last = (await http('/invite', { headers: { 'CF-Connecting-IP': '198.51.100.7' } })).status;
    expect(last).toBe(429);
    expect(
      (await http('/invite', { headers: { 'CF-Connecting-IP': '198.51.100.8' } })).status,
    ).toBe(302);
  });
  it('configures identical limits in every environment', () => {
    const config = parse(wranglerSource) as {
      ratelimits: { name: string; simple: { period: number } }[];
      env: Record<string, { ratelimits?: unknown }>;
    };
    expect(config.ratelimits.map((r) => r.name).sort()).toEqual([
      'API_RATE_LIMITER',
      'API_WRITE_RATE_LIMITER',
      'AUTH_RATE_LIMITER',
      'IMPORT_RATE_LIMITER',
      'PUBLIC_RATE_LIMITER',
    ]);
    // Retry-After advertises one period for every tier.
    expect(new Set(config.ratelimits.map((r) => r.simple.period))).toEqual(new Set([60]));
    // Same budgets everywhere; production counts in its own namespaces so staging traffic
    // cannot spend its allowance.
    const budgets = (list: unknown) =>
      (list as { name: string; simple: unknown }[]).map(({ name, simple }) => ({ name, simple }));
    const namespaces = (list: unknown) =>
      (list as { namespace_id: string }[]).map((r) => r.namespace_id);
    for (const [name, environment] of Object.entries(config.env))
      expect(budgets(environment.ratelimits), name).toEqual(budgets(config.ratelimits));
    expect(namespaces(config.env.staging.ratelimits)).toEqual(namespaces(config.ratelimits));
    for (const id of namespaces(config.env.production.ratelimits))
      expect(namespaces(config.ratelimits)).not.toContain(id);
  });
});

describe('session guild cache', () => {
  it('asks Discord once per page load and again for every change', async () => {
    await signIn();
    for (const path of [
      '/api/communities',
      `/api/communities/${guild}/config`,
      `/api/communities/${guild}/audit`,
    ])
      expect((await http(path, { headers: headers() })).status).toBe(200);
    expect(guildCalls).toBe(1);
    const saved = await http(`/api/communities/${guild}/config`, {
      method: 'PUT',
      headers: headers(),
      body: JSON.stringify({ expectedRevision: 0, settings }),
    });
    expect(saved.status).toBe(200);
    expect(guildCalls).toBe(2);
    admin = false;
    expect(
      (
        await http(`/api/communities/${guild}/config`, {
          method: 'PUT',
          headers: headers(),
          body: JSON.stringify({ expectedRevision: 1, settings }),
        })
      ).status,
    ).toBe(403);
    // The failed change refreshed the cache, so reads lose access immediately too.
    expect((await http(`/api/communities/${guild}/config`, { headers: headers() })).status).toBe(
      403,
    );
    expect(guildCalls).toBe(3);
  });
  it('expires cached guilds after the window', async () => {
    await signIn();
    await http('/api/communities', { headers: headers() });
    await runInDurableObject(env.AUTH.getByName(`session:${sessionId}`), async (_i, state) => {
      const cache = (await state.storage.get<{ at: number }>('guilds'))!;
      expect(Object.keys((cache as unknown as { guilds: object[] }).guilds[0]).sort()).toEqual([
        'icon',
        'id',
        'name',
        'owner',
        'permissions',
      ]);
      await state.storage.put('guilds', { ...cache, at: Date.now() - 31000 });
    });
    await http('/api/communities', { headers: headers() });
    expect(guildCalls).toBe(2);
  });
  it('signs the user out when Discord rejects the grant', async () => {
    await signIn();
    guildStatus = 401;
    const response = await http('/api/communities', { headers: headers() });
    expect(response.status).toBe(401);
    expect(await response.json()).toMatchObject({ code: 'sign_in_required' });
    expect(response.headers.get('Set-Cookie')).toContain('__Host-cg-session=;');
    expect(await env.AUTH.getByName(`session:${sessionId}`).session()).toBeNull();
    expect((await http('/api/communities', { headers: headers() })).status).toBe(401);
  });
  it('does not cache guilds for a session that ended', async () => {
    const store = env.AUTH.getByName(`session:${'c'.repeat(64)}`);
    await store.cacheGuilds([]);
    expect(await store.cachedGuilds(30000)).toBeNull();
  });
});

describe('transport and body limits', () => {
  it('sends HSTS on HTTPS deployments but not in local development', async () => {
    for (const path of ['/health', '/robots.txt', '/statistics/x/y'])
      expect((await http(path)).headers.get('Strict-Transport-Security'), path).toBe(
        'max-age=31536000; includeSubDomains',
      );
    const local = await app.request(
      'http://localhost:8787/health',
      {},
      {
        ...env,
        APP_ENV: 'local',
        APP_ORIGIN: 'http://localhost:8787',
      },
    );
    expect(local.headers.get('Strict-Transport-Security')).toBeNull();
  });
  it('rejects declared oversized JSON bodies before reading them', async () => {
    await signIn();
    let pulled = 0;
    const body = new ReadableStream({
      pull(controller) {
        pulled++;
        controller.enqueue(new Uint8Array(1024));
        if (pulled > 400) controller.close();
      },
    });
    const response = await app.request(
      `${base}/api/communities/${guild}/config`,
      {
        method: 'PUT',
        headers: headers({ 'Content-Length': String(300 * 1024) }),
        body,
        duplex: 'half',
      } as RequestInit,
      env,
    );
    expect(response.status).toBe(413);
    expect(await response.json()).toMatchObject({ code: 'too_large' });
    expect(pulled).toBeLessThan(5);
  });
  it('rejects undeclared oversized bodies while streaming', async () => {
    await signIn();
    const response = await http(`/api/communities/${guild}/import/preview`, {
      method: 'POST',
      headers: headers(),
      body: JSON.stringify({ padding: 'x'.repeat(5 * 1024 * 1024 + 1) }),
    });
    expect(response.status).toBe(413);
  });
  it('rejects forged sign-in callbacks without creating auth storage', async () => {
    const state = 'd'.repeat(64);
    const response = await http(`/auth/callback?state=${state}&code=x`, {
      headers: { Cookie: `cg-oauth=${state}` },
    });
    expect(response.status).toBe(400);
    expect(
      await runInDurableObject(env.AUTH.getByName(`oauth:${state}`), (_i, s) =>
        s.storage.sql.exec(`SELECT name FROM sqlite_master WHERE type='table'`).toArray(),
      ),
    ).toEqual([]);
  });
});

import { env, exports } from 'cloudflare:workers';
import {
  abortAllDurableObjects,
  reset,
  runDurableObjectAlarm,
  runInDurableObject,
} from 'cloudflare:test';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { emptyModules, settingsSchema, type Settings } from '../src/domain/config';
import { channelPermissions, type Role } from '../src/discord/client';
import { defaultRatingSettings } from '../src/domain/rating';
import { defaultRecords } from '../src/domain/records';
import { defaultMaps } from '../src/domain/maps';

const guild = '706320010368516198',
  otherGuild = '706320010368516199';
const channel = '706320010368516200',
  otherChannel = '706320010368516201';
const appId = '100000000000000001',
  userId = '100000000000000002';
const sessionId = 'a'.repeat(64),
  csrf = 'b'.repeat(64);
const base = 'https://setup.example';
const settings: Settings = {
  games: [
    { id: '4daebc0a-55e8-4d56-a17d-d94913644910', name: 'Age of Empires II', catalogId: 'aoe2' },
  ],
  queues: [
    {
      id: '81da571b-6a4a-4e5d-8e32-f4050df35a2d',
      gameId: '4daebc0a-55e8-4d56-a17d-d94913644910',
      channelId: channel,
      name: 'Pickup',
      size: 8,
      enabled: true,
      checkIn: { enabled: true, seconds: 60 },
      teamMethod: 'captain',
      ratingEngine: 'aoe2',
      modules: emptyModules,
    },
  ],
};
let admin = true,
  botPermissions = '84992',
  discordStatus = 200;
let availableRoles: Role[] = [];
let guildRows = [
  { id: guild, name: 'Friends', permissions: '32', owner: false },
  { id: otherGuild, name: 'Other friends', permissions: '32', owner: false },
];

async function http(path: string, options: RequestInit = {}) {
  return exports.default.fetch(`${base}${path}`, { redirect: 'manual', ...options });
}
async function session() {
  await env.AUTH.getByName(`session:${sessionId}`).saveSession({
    user: { id: userId, username: 'organizer', avatar: null },
    accessToken: 'test-access-token',
    csrf,
    expiresAt: Date.now() + 60000,
  });
}
const headers = () => ({
  Cookie: `__Host-cg-session=${sessionId}`,
  Origin: base,
  'X-CSRF-Token': csrf,
  'Content-Type': 'application/json',
});
async function save(g = guild, config = settings, revision = 0) {
  return http(`/api/communities/${g}/config`, {
    method: 'PUT',
    headers: headers(),
    body: JSON.stringify({ expectedRevision: revision, settings: config }),
  });
}
async function signed(
  payload: unknown,
  timestamp = String(Math.floor(Date.now() / 1000)),
  raw = JSON.stringify(payload),
) {
  const bytes = Uint8Array.from(
    '302e020100300506032b6570042204209d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60'
      .match(/../g)!
      .map((s) => parseInt(s, 16)),
  );
  const key = await crypto.subtle.importKey('pkcs8', bytes, 'Ed25519', false, ['sign']);
  const sig = await crypto.subtle.sign('Ed25519', key, new TextEncoder().encode(timestamp + raw));
  return {
    raw,
    headers: {
      'Content-Type': 'application/json',
      'X-Signature-Timestamp': timestamp,
      'X-Signature-Ed25519': Array.from(new Uint8Array(sig), (b) =>
        b.toString(16).padStart(2, '0'),
      ).join(''),
    },
  };
}
async function interaction(payload: unknown) {
  const input = await signed(payload);
  return http('/interactions', { method: 'POST', headers: input.headers, body: input.raw });
}

beforeEach(() => {
  admin = true;
  botPermissions = '84992';
  discordStatus = 200;
  availableRoles = [];
  guildRows = [
    { id: guild, name: 'Friends', permissions: '32', owner: false },
    { id: otherGuild, name: 'Other friends', permissions: '32', owner: false },
  ];
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const request = new Request(input, init),
      url = new URL(request.url);
    if (url.origin !== 'https://discord.com') throw new Error('Unexpected outbound request.');
    if (discordStatus !== 200)
      return Response.json({}, { status: discordStatus, headers: { 'Retry-After': '2' } });
    if (url.pathname === '/api/v10/oauth2/token') {
      const body = new URLSearchParams(new TextDecoder().decode(await request.arrayBuffer()));
      expect(body.get('redirect_uri')).toBe(`${base}/auth/callback`);
      expect(body.get('grant_type')).toBe('authorization_code');
      return Response.json({ access_token: 'test-access-token', expires_in: 3600 });
    }
    if (url.pathname === '/api/v10/users/@me')
      return Response.json({ id: userId, username: 'organizer', avatar: null });
    if (url.pathname === '/api/v10/users/@me/guilds') {
      expect(request.headers.get('Authorization')).toBe('Bearer test-access-token');
      return Response.json(
        guildRows.map((g) => ({ ...g, permissions: admin ? g.permissions : '0' })),
      );
    }
    const g = url.pathname.split('/')[4];
    expect(request.headers.get('Authorization')).toBe('Bot test-bot-token');
    if (url.pathname.endsWith('/channels'))
      return Response.json([
        {
          id: g === guild ? channel : otherChannel,
          guild_id: g,
          name: 'test-bot',
          type: 0,
          permission_overwrites: [],
        },
      ]);
    if (url.pathname.endsWith('/roles'))
      return Response.json([
        { id: g, name: '@everyone', permissions: botPermissions },
        ...availableRoles,
      ]);
    if (url.pathname.endsWith(`/members/${appId}`)) return Response.json({ roles: [] });
    throw new Error(`Unexpected Discord path ${url.pathname}`);
  });
});
afterEach(async () => {
  vi.restoreAllMocks();
  await reset();
});

describe('signed Discord HTTP interactions', () => {
  it('responds to a real Ed25519 PING and supplies a correlation ID', async () => {
    const response = await interaction({ type: 1, application_id: appId });
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ type: 1 });
    expect(response.headers.get('X-Request-ID')).toMatch(/^[a-f0-9-]{36}$/);
  });
  it('rejects missing signatures, tampered bodies, stale signatures and another application', async () => {
    expect((await http('/interactions', { method: 'POST', body: '{}' })).status).toBe(401);
    const input = await signed({ type: 1, application_id: appId });
    expect(
      (
        await http('/interactions', {
          method: 'POST',
          headers: input.headers,
          body: input.raw + ' ',
        })
      ).status,
    ).toBe(401);
    const stale = await signed(
      { type: 1, application_id: appId },
      String(Math.floor(Date.now() / 1000) - 301),
    );
    expect(
      (await http('/interactions', { method: 'POST', headers: stale.headers, body: stale.raw }))
        .status,
    ).toBe(401);
    expect((await interaction({ type: 1, application_id: userId })).status).toBe(401);
  });
  it('returns saved status privately after storage is restarted, and denies a non-admin', async () => {
    await env.COMMUNITIES.getByName(guild).save(guild, 0, settings, userId, 'save');
    await abortAllDurableObjects();
    const payload = {
      type: 2,
      application_id: appId,
      guild_id: guild,
      channel_id: channel,
      member: { permissions: '32' },
      data: { name: 'setup', options: [{ name: 'status' }] },
    };
    const response = await interaction(payload),
      body = (await response.json()) as {
        data: { content: string; flags: number; allowed_mentions: unknown };
      };
    expect(body.data.content).toContain('settings revision 1');
    expect(body.data.content).toContain('1 queue(s) configured in this channel');
    expect(body.data.flags).toBe(64);
    expect(body.data.allowed_mentions).toEqual({ parse: [] });
    const denied = await interaction({ ...payload, member: { permissions: '0' } });
    expect(JSON.stringify(await denied.json())).toContain('Manage Server permission');
  });
});

describe('OAuth and session security', () => {
  it('completes the authorization-code flow, rotates to a secure session and rejects state replay', async () => {
    const login = await http('/auth/login');
    expect(login.status).toBe(302);
    const url = new URL(login.headers.get('Location')!);
    expect(url.searchParams.get('scope')).toBe('identify guilds');
    const state = url.searchParams.get('state')!;
    expect(login.headers.get('Set-Cookie')).toContain('HttpOnly');
    expect(login.headers.get('Set-Cookie')).toContain('Secure');
    const callback = `/auth/callback?code=test-code&state=${state}`;
    const result = await http(callback, { headers: { Cookie: `cg-oauth=${state}` } });
    expect(result.status).toBe(302);
    const cookie = result.headers.get('Set-Cookie')!;
    expect(cookie).toContain('__Host-cg-session=');
    expect(cookie).toContain('Secure');
    expect(cookie).toContain('SameSite=Lax');
    const sid = cookie.match(/__Host-cg-session=([a-f0-9]{64})/)![1];
    const bootstrap = await http('/api/bootstrap', {
      headers: { Cookie: `__Host-cg-session=${sid}` },
    });
    const body = (await bootstrap.json()) as { user: { id: string }; csrf: string };
    expect(body.user.id).toBe(userId);
    expect(body.csrf).toHaveLength(64);
    expect(JSON.stringify(body)).not.toContain('test-access-token');
    expect((await http(callback, { headers: { Cookie: `cg-oauth=${state}` } })).status).toBe(400);
  });
  it('requires the browser-bound state cookie and rejects expired state', async () => {
    const login = await http('/auth/login'),
      state = new URL(login.headers.get('Location')!).searchParams.get('state')!;
    expect((await http(`/auth/callback?code=test&state=${state}`)).status).toBe(400);
    await env.AUTH.getByName(`oauth:${state}`).createState(Date.now() - 1000);
    expect(
      (
        await http(`/auth/callback?code=test&state=${state}`, {
          headers: { Cookie: `cg-oauth=${state}` },
        })
      ).status,
    ).toBe(400);
  });
  it('allows exactly one concurrent consumer of an OAuth state', async () => {
    const stub = env.AUTH.getByName('oauth:concurrency-test');
    await stub.createState(Date.now() + 60000);
    expect((await Promise.all([stub.consumeState(), stub.consumeState()])).sort()).toEqual([
      false,
      true,
    ]);
  });
  it('expires sessions and deletes them durably on logout', async () => {
    await session();
    expect((await http('/api/communities', { headers: headers() })).status).toBe(200);
    expect((await http('/api/logout', { method: 'POST', headers: headers() })).status).toBe(200);
    await abortAllDurableObjects();
    expect((await http('/api/communities', { headers: headers() })).status).toBe(401);
    await env.AUTH.getByName(`session:${sessionId}`).saveSession({
      user: { id: userId, username: 'organizer', avatar: null },
      accessToken: 'test-access-token',
      csrf,
      expiresAt: Date.now() - 1000,
    });
    expect((await http('/api/communities', { headers: headers() })).status).toBe(401);
    expect(await env.AUTH.getByName(`session:${sessionId}`).session()).toBeNull();
    await session();
    expect(await runDurableObjectAlarm(env.AUTH.getByName(`session:${sessionId}`))).toBe(true);
    expect(await env.AUTH.getByName(`session:${sessionId}`).session()).toBeNull();
  });
});

describe('community authority and durable settings', () => {
  const moderatorRole = '100000000000000777';
  const integrationRole = '100000000000000778';
  it('loads named human roles with setup without another Discord roles request', async () => {
    await session();
    availableRoles = [
      {
        id: integrationRole,
        name: 'Bot integration',
        permissions: '8',
        managed: true,
        position: 9,
      },
      { id: moderatorRole, name: 'Game organizers', permissions: '0', position: 2 },
      { id: '100000000000000779', name: 'Server admins', permissions: '8', position: 3 },
    ];
    const response = await http(`/api/communities/${guild}/setup`, { headers: headers() });
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({
      moderatorRoles: [
        { id: '100000000000000779', name: 'Server admins' },
        { id: moderatorRole, name: 'Game organizers' },
      ],
      channels: [{ id: channel, name: 'test-bot' }],
    });
    expect(
      vi.mocked(fetch).mock.calls.filter(([url]) => String(url).endsWith('/roles')),
    ).toHaveLength(1);
  });
  it.each(['8', '32', 'owner'])(
    'lets a server authority (%s) save moderator roles and revoke their panel access',
    async (permission) => {
      await session();
      guildRows[0].permissions = permission === 'owner' ? '0' : permission;
      guildRows[0].owner = permission === 'owner';
      availableRoles = [{ id: moderatorRole, name: 'Organizers', permissions: '0' }];
      const config = structuredClone(settings);
      config.queues[0].adminRoles = [moderatorRole];
      expect((await save(guild, config)).status).toBe(200);
      const community = env.COMMUNITIES.getByName(guild);
      const panel = () =>
        community.adminPanel(guild, {
          id: '100000000000000800',
          actor: { id: userId, name: 'Moderator', roles: [moderatorRole] },
          manager: false,
          channelId: channel,
          op: 'view',
        });
      await abortAllDurableObjects();
      expect(await panel()).toMatchObject({ ok: true, view: { canModerate: true } });
      expect((await community.read(guild)).settings.queues[0].adminRoles).toEqual([moderatorRole]);
      // Names can change; the stored Discord ID continues to identify the role.
      availableRoles[0].name = 'Renamed organizers';
      expect((await save(guild, config, 1)).status).toBe(200);
      config.queues[0].adminRoles = [];
      expect((await save(guild, config, 2)).status).toBe(200);
      expect(await panel()).toMatchObject({ ok: true, view: { canModerate: false } });
      expect((await community.audit(guild))[0]).toMatchObject({ actor_id: userId, revision: 3 });
    },
  );
  it.each(['foreign', 'everyone', 'managed'])(
    'rejects %s moderator roles before saving any settings',
    async (kind) => {
      await session();
      availableRoles = [
        { id: integrationRole, name: 'Integration', permissions: '0', managed: true },
      ];
      const config = structuredClone(settings);
      config.queues[0].adminRoles = [
        kind === 'everyone' ? guild : kind === 'managed' ? integrationRole : moderatorRole,
      ];
      expect((await save(guild, config)).status).toBe(400);
      expect((await env.COMMUNITIES.getByName(guild).read(guild)).revision).toBe(0);
    },
  );
  it('requires removal of deleted roles and denies website settings to delegated moderators', async () => {
    await session();
    availableRoles = [{ id: moderatorRole, name: 'Organizers', permissions: '0' }];
    const config = structuredClone(settings);
    config.queues[0].adminRoles = [moderatorRole];
    expect((await save(guild, config)).status).toBe(200);
    // The signed-in person is a configured bot moderator, without server permissions.
    admin = false;
    expect((await save(guild, config, 1)).status).toBe(403);
    expect((await http(`/api/communities/${guild}/setup`, { headers: headers() })).status).toBe(
      403,
    );
    admin = true;
    availableRoles = [];
    expect((await save(guild, config, 1)).status).toBe(400);
    expect(
      (await env.COMMUNITIES.getByName(guild).read(guild)).settings.queues[0].adminRoles,
    ).toEqual([moderatorRole]);
    config.queues[0].adminRoles = [];
    expect((await save(guild, config, 1)).status).toBe(200);
  });
  it('requires authentication, same-origin mutations and session-bound CSRF', async () => {
    expect((await save()).status).toBe(401);
    await session();
    const body = JSON.stringify({ expectedRevision: 0, settings });
    expect(
      (
        await http(`/api/communities/${guild}/config`, {
          method: 'PUT',
          headers: { ...headers(), Origin: 'https://evil.example' },
          body,
        })
      ).status,
    ).toBe(403);
    expect(
      (
        await http(`/api/communities/${guild}/config`, {
          method: 'PUT',
          headers: { ...headers(), 'X-CSRF-Token': 'wrong' },
          body,
        })
      ).status,
    ).toBe(403);
    expect((await env.COMMUNITIES.getByName(guild).read(guild)).revision).toBe(0);
  });
  it('rechecks administrator access on every configuration read and write', async () => {
    await session();
    expect((await save()).status).toBe(200);
    admin = false;
    for (const suffix of ['config', 'channels', 'setup', 'audit'])
      expect(
        (await http(`/api/communities/${guild}/${suffix}`, { headers: headers() })).status,
      ).toBe(403);
    expect((await save(guild, settings, 1)).status).toBe(403);
    admin = true;
    guildRows = guildRows.filter((g) => g.id !== guild);
    expect((await http(`/api/communities/${guild}/config`, { headers: headers() })).status).toBe(
      403,
    );
  });
  it('isolates communities and games, preserves renamed identities, and survives object restart', async () => {
    await session();
    expect((await save()).status).toBe(200);
    const config = structuredClone(settings);
    config.games.push({ id: crypto.randomUUID(), name: 'Custom RTS', catalogId: null });
    config.games[0].name = 'AoE friends';
    config.queues[0].keepQueueVisible = false;
    expect((await save(guild, config, 1)).status).toBe(200);
    await abortAllDurableObjects();
    const one = await env.COMMUNITIES.getByName(guild).read(guild),
      two = await env.COMMUNITIES.getByName(otherGuild).read(otherGuild);
    expect(one.revision).toBe(2);
    expect(one.settings.games).toContainEqual(config.games[0]);
    expect(one.settings.games).toContainEqual(config.games[1]);
    expect(one.settings.queues[0].keepQueueVisible).toBe(false);
    expect(two).toEqual({
      communityId: otherGuild,
      revision: 0,
      settings: { games: [], queues: [] },
    });
    const mismatch = await runInDurableObject(env.COMMUNITIES.getByName(guild), (instance) => {
      try {
        instance.read(otherGuild);
        return null;
      } catch (e) {
        return (e as Error).message;
      }
    });
    expect(mismatch).toBe('Community identity mismatch.');
  });
  it('permits only one of two concurrent saves and retains the winning revision', async () => {
    await session();
    const responses = await Promise.all([save(), save()]);
    expect(responses.map((r) => r.status).sort()).toEqual([200, 409]);
    expect((await env.COMMUNITIES.getByName(guild).read(guild)).revision).toBe(1);
    const audit = await env.COMMUNITIES.getByName(guild).audit(guild);
    expect(audit).toHaveLength(1);
    expect(audit[0].actor_id).toBe(userId);
  });
  it('rejects channels belonging to another server and refuses an enabled queue without permissions', async () => {
    await session();
    const wrong = structuredClone(settings);
    wrong.queues[0].channelId = otherChannel;
    expect((await save(guild, wrong)).status).toBe(400);
    botPermissions = '1024';
    expect((await save()).status).toBe(409);
    const disabled = structuredClone(settings);
    disabled.queues[0].enabled = false;
    expect((await save(guild, disabled)).status).toBe(200);
  });
  it('fails closed during Discord outages and returns a redacted diagnostic with Retry-After', async () => {
    await session();
    discordStatus = 429;
    const response = await save();
    expect(response.status).toBe(503);
    expect(response.headers.get('Retry-After')).toBe('2');
    const body = JSON.stringify(await response.json());
    expect(body).toContain('requestId');
    expect(body).not.toContain('test-access-token');
    expect((await env.COMMUNITIES.getByName(guild).read(guild)).revision).toBe(0);
  });
  it('recovers from a short rate limit without skipping fresh authority checks', async () => {
    await session();
    vi.mocked(fetch).mockImplementationOnce(async () =>
      Response.json({ retry_after: 0 }, { status: 429 }),
    );
    expect((await save()).status).toBe(200);
    const requests = vi.mocked(fetch).mock.calls.map(([url]) => String(url));
    expect(requests.filter((url) => url.includes('/users/@me/guilds'))).toHaveLength(2);
    expect((await env.COMMUNITIES.getByName(guild).read(guild)).revision).toBe(1);
  });
  it('combines setup reads into one fresh membership check and reports long limits without waiting', async () => {
    await session();
    expect((await http(`/api/communities/${guild}/setup`, { headers: headers() })).status).toBe(
      200,
    );
    expect(
      vi.mocked(fetch).mock.calls.filter(([url]) => String(url).includes('/users/@me/guilds')),
    ).toHaveLength(1);
    vi.mocked(fetch).mockImplementationOnce(async () =>
      Response.json({ retry_after: 25 }, { status: 429 }),
    );
    const result = await save();
    expect(result.status).toBe(503);
    expect(result.headers.get('Retry-After')).toBe('25');
    expect(((await result.json()) as { code: string }).code).toBe('discord_rate_limited');
    expect((await env.COMMUNITIES.getByName(guild).read(guild)).revision).toBe(0);
  });
  it('validates the entire configuration before writing and rejects unsupported content types', async () => {
    await session();
    const bad = structuredClone(settings);
    bad.queues[0].gameId = crypto.randomUUID();
    expect((await save(guild, bad)).status).toBe(400);
    expect(
      (
        await http(`/api/communities/${guild}/config`, {
          method: 'PUT',
          headers: { ...headers(), 'Content-Type': 'text/plain' },
          body: '{}',
        })
      ).status,
    ).toBe(415);
    expect(
      (
        await http(`/api/communities/${guild}/config`, {
          method: 'PUT',
          headers: headers(),
          body: 'x'.repeat(262145),
        })
      ).status,
    ).toBe(413);
    expect((await env.COMMUNITIES.getByName(guild).read(guild)).revision).toBe(0);
  });
});

describe('configuration feature boundaries', () => {
  it('does not expose settings export or accept restore requests from an old browser tab', async () => {
    await session();
    await save();
    const before = await env.COMMUNITIES.getByName(guild).read(guild);
    const response = await http(`/api/communities/${guild}/export`, { headers: headers() });
    expect(response.status).toBe(404);
    expect(response.headers.has('Content-Disposition')).toBe(false);
    expect(await response.json()).toMatchObject({ error: 'Unknown API route.' });
    const restore = await http(`/api/communities/${guild}/restore`, {
      method: 'POST',
      headers: headers(),
      body: JSON.stringify({
        expectedRevision: before.revision,
        backup: {
          format: 'community-gaming-settings',
          version: 1,
          sourceCommunityId: guild,
          exportedAt: new Date().toISOString(),
          settings: { games: [], queues: [] },
        },
        channelMap: {},
      }),
    });
    expect(restore.status).toBe(404);
    expect(await restore.json()).toMatchObject({ error: 'Unknown API route.' });
    expect(await env.COMMUNITIES.getByName(guild).read(guild)).toEqual(before);
    expect(await env.COMMUNITIES.getByName(guild).audit(guild)).toHaveLength(1);
  });
  it('rejects duplicate identities and prediction-without-gold flags', () => {
    const config = structuredClone(settings);
    config.games.push(config.games[0]);
    expect(settingsSchema.safeParse(config).success).toBe(false);
    config.games.pop();
    config.queues[0].modules.predictions = true;
    expect(settingsSchema.safeParse(config).success).toBe(false);
  });
});

describe('Discord permission precedence', () => {
  it('respects role and individual channel overrides, View Channel dependencies, and Administrator', () => {
    const role = '100000000000000003';
    const roles = [
      { id: guild, permissions: '84992' },
      { id: role, permissions: '0' },
    ];
    const c = {
      id: channel,
      name: 'private',
      type: 0,
      permission_overwrites: [
        { id: guild, type: 0, deny: '2048', allow: '0' },
        { id: role, type: 0, deny: '0', allow: '2048' },
        { id: appId, type: 1, deny: '1024', allow: '0' },
      ],
    };
    expect(
      channelPermissions(guild, appId, roles, { roles: [role] }, c).every((p) => !p.granted),
    ).toBe(true);
    c.permission_overwrites.pop();
    expect(
      channelPermissions(guild, appId, roles, { roles: [role] }, c).every((p) => p.granted),
    ).toBe(true);
    expect(
      channelPermissions(guild, appId, [{ id: guild, permissions: '8' }], { roles: [] }, c).every(
        (p) => p.granted,
      ),
    ).toBe(true);
  });
});

describe('responsive organizer contracts', () => {
  it.each([
    [`/?community=${guild}&view=setup`, `/?community=${guild}&view=setup`],
    ['https://evil.example/?view=setup', '/app/'],
    ['/?community=invalid&view=setup', '/app/'],
    ['/api/logout', '/app/'],
  ])('resumes an allowed organizer return path: %s', async (requested, expected) => {
    const login = await http(`/auth/login?returnTo=${encodeURIComponent(requested)}`);
    const state = new URL(login.headers.get('Location')!).searchParams.get('state')!;
    const callback = await http(`/auth/callback?code=test-code&state=${state}`, {
      headers: { Cookie: `cg-oauth=${state}` },
    });
    expect(callback.headers.get('Location')).toBe(expected);
  });
  it('advertises shipped capabilities separately from configured features', async () => {
    const response = await http('/api/bootstrap');
    expect(((await response.json()) as { capabilities: unknown }).capabilities).toEqual({
      matchmaking: true,
      gold: true,
      predictions: true,
      maps: true,
      civs: true,
      stories: true,
      quiz: false,
    });
  });
  it('generates minimal and feature-scoped invite permissions without Manage Channels', async () => {
    const response = await http('/api/bootstrap');
    const body = (await response.json()) as {
      inviteUrl: string;
      inviteUrls: Record<string, string>;
    };
    expect(body.inviteUrl).toBe(body.inviteUrls.basic);
    for (const [feature, extra] of [
      ['basic', 0n],
      ['ranks', 1n << 28n],
      ['nicknames', 1n << 27n],
    ] as const) {
      const url = new URL(body.inviteUrls[feature]);
      expect(url.origin).toBe('https://discord.com');
      expect(url.searchParams.get('client_id')).toBe(appId);
      expect(url.searchParams.get('scope')).toBe('bot applications.commands');
      const bits = BigInt(url.searchParams.get('permissions')!);
      expect(bits).toBe(84992n | extra);
      expect(bits & (8n | 16n)).toBe(0n);
    }
  });
  it.each([403, 404, 503])(
    'keeps saved setup available when bot discovery returns %i',
    async (status) => {
      await session();
      expect((await save()).status).toBe(200);
      const transport = vi.mocked(fetch).getMockImplementation()!;
      vi.mocked(fetch).mockImplementation(async (input, init) => {
        if (String(input).includes(`/guilds/${guild}/roles`)) return Response.json({}, { status });
        return transport(input, init);
      });
      const response = await http(`/api/communities/${guild}/setup`, { headers: headers() });
      expect(response.status).toBe(200);
      const body = (await response.json()) as {
        snapshot: { revision: number };
        access: { status: string };
        channels: unknown[];
        gameState: unknown[];
      };
      expect(body.snapshot.revision).toBe(1);
      expect(body.access.status).toBe(status === 503 ? 'unavailable' : 'needs_access');
      expect(body.channels).toEqual([]);
      expect(body.gameState).toEqual([
        { id: settings.games[0].id, ratingEngineLocked: false, ratingEngine: 'aoe2' },
      ]);
      admin = false;
      expect((await http(`/api/communities/${guild}/setup`, { headers: headers() })).status).toBe(
        403,
      );
    },
  );
  it('returns field paths and distinguishes domain conflicts from stale revisions', async () => {
    await session();
    const invalid = structuredClone(settings);
    invalid.queues[0].size = 1;
    const validation = await save(guild, invalid);
    expect(validation.status).toBe(400);
    expect(await validation.json()).toMatchObject({
      code: 'invalid_settings',
      issues: [{ path: ['settings', 'queues', 0, 'size'] }],
    });
    expect((await save()).status).toBe(200);
    const mismatch = structuredClone(settings);
    mismatch.queues.push({
      ...mismatch.queues[0],
      id: crypto.randomUUID(),
      name: 'Other queue',
      ratingEngine: 'flat',
    });
    expect(await (await save(guild, mismatch, 1)).json()).toMatchObject({
      code: 'settings_conflict',
      issues: expect.arrayContaining([
        expect.objectContaining({ path: ['settings', 'queues', 1, 'ratingEngine'] }),
      ]),
    });
    expect(await (await save(guild, settings, 0)).json()).toMatchObject({
      code: 'revision_conflict',
    });
  });
  it('returns paths for cross-field rating, map and rank validation', async () => {
    await session();
    const invalid = structuredClone(settings);
    invalid.queues[0].ratingSettings = {
      ...defaultRatingSettings,
      deviation: 100,
      minDeviation: 200,
    };
    invalid.queues[0].maps = { ...defaultMaps(['Arena', 'arena']), count: 3 };
    invalid.games[0].records = {
      ...structuredClone(defaultRecords),
      ranks: [
        { threshold: 0, label: 'First', roleId: null },
        { threshold: 0, label: 'Duplicate', roleId: null },
      ],
    };
    const response = await save(guild, invalid);
    expect(response.status).toBe(400);
    expect(await response.json()).toMatchObject({
      code: 'invalid_settings',
      requestId: expect.any(String),
      issues: expect.arrayContaining([
        expect.objectContaining({
          path: ['settings', 'queues', 0, 'ratingSettings', 'minDeviation'],
        }),
        expect.objectContaining({ path: ['settings', 'queues', 0, 'maps', 'pool'] }),
        expect.objectContaining({ path: ['settings', 'queues', 0, 'maps', 'count'] }),
        expect.objectContaining({
          path: ['settings', 'games', 0, 'records', 'ranks', 0, 'threshold'],
        }),
        expect.objectContaining({
          path: ['settings', 'games', 0, 'records', 'ranks', 1, 'threshold'],
        }),
      ]),
    });
  });
  it('identifies unavailable roles and the channel needing permissions without revision conflicts', async () => {
    await session();
    const invalid = structuredClone(settings),
      missing = '100000000000000099';
    invalid.queues[0].controls = {
      requiredRole: missing,
      blockedRole: null,
      waitingSeconds: 0,
      matchSeconds: 0,
    };
    invalid.games[0].records = {
      ...structuredClone(defaultRecords),
      ranks: [{ threshold: 0, label: 'Rank', roleId: missing }],
    };
    const response = await save(guild, invalid);
    expect(response.status).toBe(400);
    expect(await response.json()).toMatchObject({
      code: 'wrong_role',
      requestId: expect.any(String),
      issues: [
        expect.objectContaining({ path: ['settings', 'queues', 0, 'controls', 'requiredRole'] }),
        expect.objectContaining({
          path: ['settings', 'games', 0, 'records', 'ranks', 0, 'roleId'],
        }),
      ],
    });
    botPermissions = '1024';
    const permissions = await save();
    expect(permissions.status).toBe(409);
    expect(await permissions.json()).toMatchObject({
      code: 'missing_permissions',
      issues: [expect.objectContaining({ path: ['settings', 'queues', 0, 'channelId'] })],
    });
    expect((await env.COMMUNITIES.getByName(guild).read(guild)).revision).toBe(0);
  });
});

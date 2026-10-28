import { env } from 'cloudflare:workers';
import { reset, runInDurableObject } from 'cloudflare:test';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import app from '../src/worker';
import type { Settings } from '../src/domain/config';

const guild = '706320010368516198',
  game = '4daebc0a-55e8-4d56-a17d-d94913644910';
const base = 'https://setup.example',
  user = '100000000000000100';
const sessionId = 'e'.repeat(64),
  csrf = 'f'.repeat(64);
let member = true,
  steamStatus = 200,
  steamCalls = 0;
const config = (): Settings => ({
  games: [{ id: game, name: 'Northgard', catalogId: 'northgard' }],
  queues: [],
});
const headers = () => ({
  Cookie: `__Host-cg-session=${sessionId}`,
  Origin: base,
  'X-CSRF-Token': csrf,
  'Content-Type': 'application/json',
});
const request = (path: string, init: RequestInit = {}) => app.request(`${base}${path}`, init, env);
async function begin() {
  const response = await request('/auth/game/start', {
    method: 'POST',
    headers: headers(),
    body: JSON.stringify({ communityId: guild, gameId: game, provider: 'steam' }),
  });
  expect(response.status).toBe(200);
  const result = (await response.json()) as { url: string };
  return new URL(result.url).searchParams.get('openid.return_to')!;
}
function callback(returnTo: string) {
  const params = new URL(returnTo).searchParams;
  for (const [key, value] of Object.entries({
    'openid.ns': 'http://specs.openid.net/auth/2.0',
    'openid.mode': 'id_res',
    'openid.op_endpoint': 'https://steamcommunity.com/openid/login',
    'openid.claimed_id': 'https://steamcommunity.com/openid/id/76561198000000001',
    'openid.identity': 'https://steamcommunity.com/openid/id/76561198000000001',
    'openid.return_to': returnTo,
    'openid.response_nonce': `${new Date().toISOString().replace(/\.\d{3}Z$/, 'Z')}signed-nonce`,
    'openid.assoc_handle': 'handle',
    'openid.sig': 'signature',
    'openid.signed': 'op_endpoint,claimed_id,identity,return_to,response_nonce,assoc_handle',
  }))
    params.set(key, value);
  return `/auth/game/callback/steam?${params}`;
}
beforeEach(async () => {
  member = true;
  steamStatus = 200;
  steamCalls = 0;
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const req = new Request(input, init),
      url = new URL(req.url);
    if (url.hostname === 'discord.com' && url.pathname.endsWith('/users/@me/guilds'))
      return Response.json(
        member ? [{ id: guild, name: 'Members', permissions: '0', owner: false }] : [],
      );
    if (url.href === 'https://steamcommunity.com/openid/login') {
      steamCalls++;
      expect(req.redirect).toBe('manual');
      return steamStatus === 200
        ? new Response('ns:http://specs.openid.net/auth/2.0\nis_valid:true\n')
        : new Response(null, { status: steamStatus });
    }
    throw new Error(`Unexpected network destination: ${url.hostname}${url.pathname}`);
  });
  await env.AUTH.getByName(`session:${sessionId}`).saveSession({
    user: { id: user, username: 'First-time member', avatar: null },
    accessToken: 'test-discord',
    csrf,
    expiresAt: Date.now() + 600000,
  });
  await env.COMMUNITIES.getByName(guild).save(guild, 0, config(), user, 'save');
});
afterEach(async () => {
  vi.restoreAllMocks();
  await reset();
});

async function noParticipation() {
  await runInDurableObject(env.COMMUNITIES.getByName(guild), (_instance, state) => {
    for (const table of ['profiles', 'participants', 'rating_changes', 'wallets'])
      expect(
        state.storage.sql.exec<{ n: number }>(`SELECT COUNT(*) AS n FROM ${table}`).toArray()[0].n,
      ).toBe(0);
  });
}
describe('game identity routes through the real Worker and Community store', () => {
  it('allows a non-admin member to verify Steam before any game participation', async () => {
    const returnTo = await begin();
    const response = await request(callback(returnTo), { headers: headers() });
    expect(response.status).toBe(200);
    expect(response.headers.get('Referrer-Policy')).toBe('no-referrer');
    expect(steamCalls).toBe(1);
    await runInDurableObject(env.COMMUNITIES.getByName(guild), (_instance, state) => {
      expect(
        state.storage.sql
          .exec('SELECT provider,external_id,user_id,trust,verified_by FROM profile_claims')
          .toArray(),
      ).toEqual([
        {
          provider: 'steam',
          external_id: '76561198000000001',
          user_id: user,
          trust: 'ownership_verified',
          verified_by: 'steam-openid',
        },
      ]);
    });
    await noParticipation();
    expect((await request(callback(returnTo), { headers: headers() })).status).toBe(400);
    expect(steamCalls).toBe(1);
  });
  it('requires current community membership on start and rechecks it on callback', async () => {
    member = false;
    expect(
      (
        await request('/auth/game/start', {
          method: 'POST',
          headers: headers(),
          body: JSON.stringify({ communityId: guild, gameId: game, provider: 'steam' }),
        })
      ).status,
    ).toBe(403);
    member = true;
    const returnTo = await begin();
    member = false;
    expect((await request(callback(returnTo), { headers: headers() })).status).toBe(403);
    expect(steamCalls).toBe(0);
    await noParticipation();
  });
  it('honors an organizer disabling ownership verification after sign-in starts', async () => {
    const returnTo = await begin();
    const store = env.COMMUNITIES.getByName(guild),
      snapshot = await store.read(guild);
    const updated = config();
    updated.games[0].connector = { enabled: false, capabilities: { ownershipVerification: false } };
    await store.save(guild, snapshot.revision, updated, user, 'save');
    const response = await request(callback(returnTo), { headers: headers() });
    expect(response.status).toBe(503);
    expect(await response.json()).toMatchObject({ code: 'disabled' });
    expect(steamCalls).toBe(0);
    await noParticipation();
  });
  it('does not store ownership when the provider rejects authorization', async () => {
    const returnTo = await begin();
    steamStatus = 401;
    const response = await request(callback(returnTo), { headers: headers() });
    expect(response.status).toBe(401);
    expect(await response.json()).toMatchObject({ code: 'authorization_revoked' });
    await runInDurableObject(env.COMMUNITIES.getByName(guild), (_instance, state) => {
      expect(state.storage.sql.exec('SELECT * FROM profile_claims').toArray()).toEqual([]);
    });
    await noParticipation();
  });
});

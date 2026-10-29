import { env } from 'cloudflare:workers';
import { reset, runInDurableObject } from 'cloudflare:test';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import app from '../src/worker';
import type { Env } from '../src/env';
import type { Settings } from '../src/domain/config';
import {
  authorizationUrl,
  completeGameOAuth,
  supportsOwnership,
  type GameVerificationState,
} from '../src/auth/game-accounts';
import { wc3AccountNamespace, wc3Edition, wc3ProfileUrl } from '../src/games/wc3';

const guild = '706320010368516198';
const game = '4daebc0a-55e8-4d56-a17d-d94913644910';
const base = 'https://setup.example';
const user = '100000000000000100';
const otherUser = '100000000000000101';
const sessionId = 'e'.repeat(64);
const otherSession = 'd'.repeat(64);
const csrf = 'f'.repeat(64);
const subject = '9007199254740993';
const battleTag = 'Äther#1234';
const credentials = {
  BATTLENET_CLIENT_ID: 'wc3-fixture-client',
  BATTLENET_CLIENT_SECRET: 'wc3-fixture-secret',
};
const token = 'wc3-fixture-access-token-never-store';
const community = () => env.COMMUNITIES.getByName(guild);
const config = (): Settings => ({
  games: [{ id: game, name: 'Warcraft III', catalogId: 'wc3' }],
  queues: [],
});
const state = (): GameVerificationState => ({
  provider: 'battlenet',
  communityId: guild,
  gameId: game,
  catalogId: 'wc3',
  discordUserId: user,
  sessionId,
  expiresAt: Date.now() + 600000,
  redirectUri: `${base}/auth/game/callback/battlenet`,
});
let member = true;
let configured = true;
let identity: Record<string, unknown> = {};
let tokenStatus = 200;
let userinfoStatus = 200;
const bindings = () => ({
  ...env,
  BATTLENET_CLIENT_ID: configured ? credentials.BATTLENET_CLIENT_ID : undefined,
  BATTLENET_CLIENT_SECRET: configured ? credentials.BATTLENET_CLIENT_SECRET : undefined,
});
const headers = (other = false) => ({
  Cookie: `__Host-cg-session=${other ? otherSession : sessionId}`,
  Origin: base,
  'X-CSRF-Token': csrf,
  'Content-Type': 'application/json',
});
const request = (path: string, init: RequestInit = {}) =>
  app.request(`${base}${path}`, init, bindings());
const authCalls = () =>
  vi
    .mocked(fetch)
    .mock.calls.map(([input]) => new URL(input instanceof Request ? input.url : String(input)))
    .filter((url) => url.hostname === 'oauth.battle.net');
async function begin(other = false) {
  const response = await request('/auth/game/start', {
    method: 'POST',
    headers: headers(other),
    body: JSON.stringify({ communityId: guild, gameId: game, provider: 'battlenet' }),
  });
  expect(response.status).toBe(200);
  const result = (await response.json()) as { url: string };
  const url = new URL(result.url);
  expect(url.searchParams.get('scope')).toBe('openid');
  return url.searchParams.get('state')!;
}
const callback = (nonce: string, other = false) =>
  request(`/auth/game/callback/battlenet?state=${nonce}&code=fixture-code`, {
    headers: headers(other),
  });
const claims = () =>
  runInDurableObject(community(), (_instance, durable) =>
    durable.storage.sql
      .exec(
        'SELECT provider,external_id,user_id,trust,namespace,edition,platform_id,verified_by FROM profile_claims',
      )
      .toArray(),
  );
async function noParticipation() {
  await runInDurableObject(community(), (_instance, durable) => {
    for (const table of [
      'profiles',
      'matches',
      'participants',
      'rating_changes',
      'wallets',
      'gold_ledger',
    ])
      expect(durable.storage.sql.exec(`SELECT * FROM ${table}`).toArray()).toEqual([]);
  });
}
async function communityCredentials() {
  await runInDurableObject(community(), (instance) => {
    // Test-only bindings on this isolated DO instance; no credentials are persisted.
    const scope = instance as unknown as { env: Env };
    scope.env = {
      ...scope.env,
      BATTLENET_CLIENT_ID: configured ? credentials.BATTLENET_CLIENT_ID : undefined,
      BATTLENET_CLIENT_SECRET: configured ? credentials.BATTLENET_CLIENT_SECRET : undefined,
    };
  });
}
async function declare(tag = battleTag) {
  return community().linkGameProfile(
    guild,
    {
      id: crypto.randomUUID(),
      kind: 'link',
      gameId: game,
      userId: user,
      provider: 'w3champions',
      externalId: tag,
    },
    user,
    false,
  );
}

beforeEach(async () => {
  member = true;
  configured = true;
  tokenStatus = 200;
  userinfoStatus = 200;
  identity = {
    sub: subject,
    battletag: battleTag,
    preferred_username: 'DifferentDisplayName#9999',
  };
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const req = new Request(input, init);
    const url = new URL(req.url);
    if (url.hostname === 'discord.com' && url.pathname.endsWith('/users/@me/guilds'))
      return Response.json(
        member ? [{ id: guild, name: 'Members', permissions: '0', owner: false }] : [],
      );
    if (url.href === 'https://oauth.battle.net/token') {
      expect(req.redirect).toBe('manual');
      expect(req.method).toBe('POST');
      expect(req.headers.get('Authorization')).toBe(
        `Basic ${btoa(`${credentials.BATTLENET_CLIENT_ID}:${credentials.BATTLENET_CLIENT_SECRET}`)}`,
      );
      const form = await req.formData();
      expect(form.get('grant_type')).toBe('authorization_code');
      expect(form.get('redirect_uri')).toBe(state().redirectUri);
      return tokenStatus === 200
        ? Response.json({ access_token: token, token_type: 'Bearer' })
        : new Response(null, { status: tokenStatus });
    }
    if (url.href === 'https://oauth.battle.net/userinfo') {
      expect(req.redirect).toBe('manual');
      expect(req.headers.get('Authorization')).toBe(`Bearer ${token}`);
      return userinfoStatus === 200
        ? Response.json(identity)
        : new Response(null, { status: userinfoStatus });
    }
    if (
      url.hostname === 'website-backend.w3champions.com' &&
      url.pathname.startsWith('/api/players/')
    ) {
      const tag = decodeURIComponent(url.pathname.slice('/api/players/'.length));
      // A public profile, including any unrelated platform-looking fields, is not ownership proof.
      return Response.json({ battleTag: tag, name: tag.split('#')[0], platformId: '987654321' });
    }
    throw new Error(`Unexpected request to ${url.hostname}${url.pathname}`);
  });
  for (const [id, discord] of [
    [sessionId, user],
    [otherSession, otherUser],
  ]) {
    await env.AUTH.getByName(`session:${id}`).saveSession({
      user: { id: discord, username: 'Fixture member', avatar: null },
      accessToken: `discord-fixture-${discord}`,
      csrf,
      expiresAt: Date.now() + 600000,
    });
  }
  expect(await community().save(guild, 0, config(), user, 'save')).toMatchObject({ ok: true });
  await communityCredentials();
});
afterEach(async () => {
  vi.restoreAllMocks();
  await reset();
});

describe('Warcraft Battle.net identity contract', () => {
  it('requests only OpenID for Warcraft while retaining legacy SC2 character scope', async () => {
    expect(supportsOwnership('wc3', 'battlenet')).toBe(true);
    expect(supportsOwnership('wc3', 'steam')).toBe(false);
    expect(supportsOwnership('wc3', 'bar')).toBe(false);
    const url = new URL(await authorizationUrl(state(), 'a'.repeat(64), credentials));
    expect(url.searchParams.get('scope')).toBe('openid');
    expect(url.href).not.toContain(credentials.BATTLENET_CLIENT_SECRET);
    const legacy = new URL(
      await authorizationUrl({ ...state(), catalogId: 'sc2' }, 'a'.repeat(64), credentials),
    );
    expect(legacy.searchParams.get('scope')).toBe('openid sc2.profile');
  });
  it('keeps a lossless stable subject separate from the exact authenticated BattleTag', async () => {
    const result = await completeGameOAuth(state(), 'fixture-code', credentials);
    expect(result).toEqual({ subject, name: battleTag, battleTag, accessToken: token });
    expect(authCalls().map((url) => url.pathname)).toEqual(['/token', '/userinfo']);
  });
  it.each([
    undefined,
    null,
    ['Player#1234'],
    'MissingDiscriminator',
    'Player#notanumber',
    'https://w3champions.com/player/Player%231234',
    ' Player#1234 ',
    'Player\n#1234',
  ])(
    'rejects an unusable authenticated BattleTag without substituting another identity: %j',
    async (value) => {
      identity.battletag = value;
      await expect(completeGameOAuth(state(), 'fixture-code', credentials)).rejects.toMatchObject({
        code: 'invalid_response',
      });
      expect(await claims()).toEqual([]);
    },
  );
  it.each([undefined, null, '0', '-1', 'not-a-subject'])(
    'rejects invalid stable subjects: %j',
    async (value) => {
      identity.sub = value;
      await expect(completeGameOAuth(state(), 'fixture-code', credentials)).rejects.toMatchObject({
        code: 'invalid_response',
      });
    },
  );
});

describe('Warcraft verification through real sessions and SQLite', () => {
  it('links a first-time member using userinfo only, retains subject and stores no OAuth token', async () => {
    const nonce = await begin();
    const response = await callback(nonce);
    expect(response.status).toBe(200);
    const html = await response.text();
    expect(html).toContain('Account verified');
    expect(html).not.toContain('SC2');
    expect(html).not.toContain(token);
    expect(response.headers.get('Referrer-Policy')).toBe('no-referrer');
    expect((await claims()).filter((row) => row.provider === 'w3champions')).toEqual([
      {
        provider: 'w3champions',
        external_id: battleTag,
        user_id: user,
        trust: 'ownership_verified',
        namespace: wc3AccountNamespace,
        edition: wc3Edition,
        platform_id: subject,
        verified_by: 'battlenet-oauth',
      },
    ]);
    const player = (await community().statistics(guild, game, user, 0, true, user)).player;
    expect(player?.profiles.find((p) => p.provider === 'w3champions')).toMatchObject({
      external_id: battleTag,
      platformId: subject,
      url: wc3ProfileUrl(battleTag),
      trust: 'ownership_verified',
    });
    expect(authCalls().map((url) => url.pathname)).toEqual(['/token', '/userinfo']);
    expect(
      vi
        .mocked(fetch)
        .mock.calls.some(([input]) =>
          String(input instanceof Request ? input.url : input).includes('/sc2/'),
        ),
    ).toBe(false);
    const backup = JSON.stringify(await community().operationalBackup(guild));
    expect(backup).not.toContain(token);
    expect(backup).not.toContain('fixture-code');
    await noParticipation();
    expect((await callback(nonce)).status).toBe(400);
    expect(authCalls()).toHaveLength(2);
  });
  it('preserves the initiating Discord session and rechecks membership before provider reads', async () => {
    const nonce = await begin();
    expect((await callback(nonce, true)).status).toBe(400);
    expect(authCalls()).toHaveLength(0);
    member = false;
    expect((await callback(nonce)).status).toBe(403);
    expect(authCalls()).toHaveLength(0);
    expect(await claims()).toEqual([]);
  });
  it('rejects a cross-origin start and an expired callback before exchanging credentials', async () => {
    expect(
      (
        await request('/auth/game/start', {
          method: 'POST',
          headers: { ...headers(), Origin: 'https://other.example' },
          body: JSON.stringify({ communityId: guild, gameId: game, provider: 'battlenet' }),
        })
      ).status,
    ).toBe(403);
    const nonce = 'a'.repeat(64);
    await env.AUTH.getByName(`game-oauth:${nonce}`).createGameVerification({
      ...state(),
      expiresAt: Date.now() - 1,
    });
    expect((await callback(nonce)).status).toBe(400);
    expect(authCalls()).toHaveLength(0);
    expect(await claims()).toEqual([]);
  });
  it('rejects revoked userinfo authorization without upgrading a declared link', async () => {
    expect(await declare()).toMatchObject({ ok: true });
    const nonce = await begin();
    userinfoStatus = 401;
    expect((await callback(nonce)).status).toBe(401);
    expect((await claims()).filter((row) => row.provider === 'w3champions')).toMatchObject([
      { trust: 'declared', platform_id: null },
    ]);
    await noParticipation();
  });
  it('keeps public lookup and declared links available without OAuth credentials', async () => {
    configured = false;
    await communityCredentials();
    const response = await request('/auth/game/start', {
      method: 'POST',
      headers: headers(),
      body: JSON.stringify({ communityId: guild, gameId: game, provider: 'battlenet' }),
    });
    expect(response.status).toBe(503);
    expect(await response.json()).toMatchObject({ code: 'credentials_required' });
    expect(await declare()).toMatchObject({ ok: true });
    expect((await claims()).filter((row) => row.provider === 'w3champions')).toMatchObject([
      {
        external_id: battleTag,
        user_id: user,
        trust: 'declared',
        platform_id: null,
      },
    ]);
    expect(authCalls()).toHaveLength(0);
    await noParticipation();
  });
  it('rejects another Discord user claiming an existing BattleTag', async () => {
    expect((await callback(await begin())).status).toBe(200);
    identity.sub = '234567890';
    const response = await callback(await begin(true), true);
    expect(response.status).toBe(409);
    expect((await claims()).filter((row) => row.provider === 'w3champions')).toMatchObject([
      {
        external_id: battleTag,
        user_id: user,
        platform_id: subject,
        trust: 'ownership_verified',
      },
    ]);
    await noParticipation();
  });
  it('cannot use a renamed BattleTag to give another Discord user the same stable Battle.net account', async () => {
    expect((await callback(await begin())).status).toBe(200);
    identity.battletag = 'Renamed#5678';
    const response = await callback(await begin(true), true);
    expect(response.status).toBe(409);
    expect((await claims()).filter((row) => row.provider === 'w3champions')).toMatchObject([
      {
        external_id: battleTag,
        user_id: user,
        platform_id: subject,
      },
    ]);
    expect((await claims()).some((row) => row.user_id === otherUser)).toBe(false);
  });
  it('requires the owner to explicitly unlink a previous BattleTag before verifying a rename', async () => {
    expect((await callback(await begin())).status).toBe(200);
    identity.battletag = 'Renamed#5678';
    const changed = await callback(await begin());
    expect(changed.status).toBe(409);
    expect(await changed.text()).toContain('BattleTag changed');
    expect((await claims()).filter((row) => row.provider === 'w3champions')).toMatchObject([
      { external_id: battleTag, user_id: user, platform_id: subject },
    ]);
    expect(
      await community().recordAction(
        guild,
        {
          id: crypto.randomUUID(),
          kind: 'unlink',
          gameId: game,
          userId: user,
          provider: 'w3champions',
          externalId: battleTag,
        },
        user,
        false,
      ),
    ).toMatchObject({ ok: true });
    expect((await callback(await begin())).status).toBe(200);
    expect((await claims()).filter((row) => row.provider === 'w3champions')).toEqual([
      {
        provider: 'w3champions',
        external_id: 'Renamed#5678',
        user_id: user,
        platform_id: subject,
        trust: 'ownership_verified',
        namespace: wc3AccountNamespace,
        edition: wc3Edition,
        verified_by: 'battlenet-oauth',
      },
    ]);
    await noParticipation();
  });
});

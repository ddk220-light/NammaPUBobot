import { env } from 'cloudflare:workers';
import { reset, runInDurableObject } from 'cloudflare:test';
import { Hono } from 'hono';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  authorizationUrl,
  battleNetClientToken,
  completeGameOAuth,
  gameAccountAvailability,
  pkceChallenge,
  steamEndpoint,
  verifySteamAssertion,
  type GameVerificationState,
} from '../src/auth/game-accounts';
import { gameAccountRoutes, type GameAccountApp } from '../src/routes/game-accounts';
import type { CatalogId } from '../src/games/types';
import type { Session } from '../src/env';

const guild = '706320010368516198',
  game = '4daebc0a-55e8-4d56-a17d-d94913644910';
const userId = '100000000000000100',
  sessionId = 'a'.repeat(64),
  csrf = 'b'.repeat(64);
const origin = 'https://bot.example';
const nonce = 'c'.repeat(64);
const session: Session = {
  user: { id: userId, username: 'Player', avatar: null },
  accessToken: 'discord-test-token',
  csrf,
  expiresAt: Date.now() + 3600000,
};
const state = (provider: GameVerificationState['provider'] = 'steam'): GameVerificationState => ({
  provider,
  communityId: guild,
  gameId: game,
  catalogId: provider === 'steam' ? 'northgard' : provider === 'bar' ? 'bar' : 'sc2',
  discordUserId: userId,
  sessionId,
  expiresAt: Date.now() + 600000,
  redirectUri: `${origin}/auth/game/callback/${provider}${provider === 'steam' ? `?state=${nonce}` : ''}`,
  ...(provider === 'bar' ? { codeVerifier: 'd'.repeat(64) } : {}),
});
function assertion(s = state()) {
  return new URLSearchParams({
    state: nonce,
    'openid.ns': 'http://specs.openid.net/auth/2.0',
    'openid.mode': 'id_res',
    'openid.op_endpoint': steamEndpoint,
    'openid.claimed_id': 'https://steamcommunity.com/openid/id/76561198000000001',
    'openid.identity': 'https://steamcommunity.com/openid/id/76561198000000001',
    'openid.return_to': s.redirectUri,
    'openid.response_nonce': `${new Date().toISOString().replace(/\.\d{3}Z$/, 'Z')}random`,
    'openid.assoc_handle': 'association',
    'openid.signed': 'op_endpoint,claimed_id,identity,return_to,response_nonce,assoc_handle',
    'openid.sig': 'test-signature',
  });
}
const steamSuccess = () => new Response('ns:http://specs.openid.net/auth/2.0\nis_valid:true\n');
let catalog: CatalogId = 'northgard',
  signedIn = true,
  otherSession = false;
let authorize = vi.fn(async (..._args: unknown[]) => ({ catalogId: catalog }));
let link = vi.fn(async (..._args: unknown[]) => ({ ok: true, content: 'Ownership verified.' }));
const bindings = () => ({
  ...env,
  AUTH: env.AUTH,
  APP_ENV: 'local' as const,
  APP_ORIGIN: origin,
  BATTLENET_CLIENT_ID: 'bn-client',
  BATTLENET_CLIENT_SECRET: 'bn-secret',
  BAR_CLIENT_ID: 'bar-client',
  BAR_CLIENT_SECRET: 'bar-secret',
});
function router() {
  const app = new Hono<GameAccountApp>();
  app.route(
    '/auth/game',
    gameAccountRoutes({
      session: async () =>
        signedIn ? { id: otherSession ? 'other-session' : sessionId, session } : null,
      authorize: async (...args) => authorize(...args),
      link: async (...args) => link(...args),
    }),
  );
  return app;
}
beforeEach(() => {
  catalog = 'northgard';
  signedIn = true;
  otherSession = false;
  authorize = vi.fn(async (..._args: unknown[]) => ({ catalogId: catalog }));
  link = vi.fn(async (..._args: unknown[]) => ({
    ok: true,
    content: 'Ownership verified. No ratings or participation were awarded.',
  }));
});
afterEach(async () => {
  vi.restoreAllMocks();
  await reset();
});

describe('game account verification provider helpers', () => {
  it('constructs Steam OpenID with an exact callback and fixed provider', async () => {
    const url = new URL(await authorizationUrl(state(), nonce, {}));
    expect(`${url.origin}${url.pathname}`).toBe(steamEndpoint);
    expect(url.searchParams.get('openid.return_to')).toBe(state().redirectUri);
    expect(url.searchParams.get('openid.realm')).toBe(origin);
  });
  it('verifies Steam signatures at the fixed provider and preserves the Steam64 string', async () => {
    const request = vi.fn<typeof fetch>().mockResolvedValue(steamSuccess());
    const id = await verifySteamAssertion(assertion(), state(), Date.now(), request);
    expect(id).toBe('76561198000000001');
    expect(request.mock.calls[0][0]).toBe(steamEndpoint);
    const init = request.mock.calls[0][1]!;
    expect(init.redirect).toBe('manual');
    const body = init.body as URLSearchParams;
    expect(body.get('openid.mode')).toBe('check_authentication');
    expect(body.has('state')).toBe(false);
  });
  it.each([
    ['openid.op_endpoint', 'https://attacker.example/openid'],
    ['openid.return_to', `${origin}/auth/game/callback/steam?state=other`],
    ['openid.claimed_id', 'https://steamcommunity.com.evil/openid/id/76561198000000001'],
    ['openid.identity', 'https://steamcommunity.com/openid/id/76561198000000002'],
    ['openid.signed', 'identity,return_to'],
    ['openid.response_nonce', '2000-01-01T00:00:00Zold'],
  ])(
    'rejects a forged or stale Steam assertion field %s before network work',
    async (key, value) => {
      const query = assertion();
      query.set(key, value);
      const request = vi.fn<typeof fetch>();
      await expect(verifySteamAssertion(query, state(), Date.now(), request)).rejects.toMatchObject(
        { code: 'invalid_assertion' },
      );
      expect(request).not.toHaveBeenCalled();
    },
  );
  it('rejects duplicate OpenID keys and provider signature rejection', async () => {
    const query = assertion();
    query.append('openid.identity', query.get('openid.identity')!);
    await expect(verifySteamAssertion(query, state())).rejects.toMatchObject({
      code: 'invalid_assertion',
    });
    const request = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response('ns:http://specs.openid.net/auth/2.0\nis_valid:false\n'));
    await expect(
      verifySteamAssertion(assertion(), state(), Date.now(), request),
    ).rejects.toMatchObject({ code: 'invalid_assertion' });
  });
  it('gates OAuth on configured clients and uses BAR S256 with the minimal profile scope', async () => {
    expect(gameAccountAvailability('battlenet', {})).toBe(false);
    expect(gameAccountAvailability('bar', {})).toBe(false);
    expect(gameAccountAvailability('bar', { BAR_CLIENT_ID: 'public-pkce-client' })).toBe(true);
    const s = state('bar'),
      url = new URL(await authorizationUrl(s, nonce, bindings()));
    expect(url.searchParams.get('scope')).toBe('profile');
    expect(url.searchParams.get('code_challenge_method')).toBe('S256');
    expect(url.searchParams.get('code_challenge')).toBe(await pkceChallenge(s.codeVerifier!));
    expect(url.searchParams.get('code_verifier')).toBeNull();
    expect(url.href).not.toContain('bar-secret');
    const bn = new URL(await authorizationUrl(state('battlenet'), nonce, bindings()));
    expect(bn.searchParams.get('scope')).toBe('openid sc2.profile');
    expect(bn.searchParams.get('code_challenge')).toBeNull();
  });
  it('exchanges BAR authorization in memory and reads only stable userinfo identity', async () => {
    const request = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        Response.json({ access_token: 'private-access-token', token_type: 'Bearer' }),
      )
      .mockResolvedValueOnce(
        Response.json({ sub: '9007199254740993', preferred_username: 'BAR Player' }),
      );
    expect(
      await completeGameOAuth(state('bar'), 'authorization-code', bindings(), request),
    ).toEqual({
      subject: '9007199254740993',
      name: 'BAR Player',
      accessToken: 'private-access-token',
    });
    expect(request.mock.calls.map(([url]) => url)).toEqual([
      'https://server4.beyondallreason.info/oauth/token',
      'https://server4.beyondallreason.info/oauth/userinfo',
    ]);
    const tokenBody = request.mock.calls[0][1]!.body as URLSearchParams;
    expect(tokenBody.get('code_verifier')).toBe(state('bar').codeVerifier);
    expect(request.mock.calls[1][1]!.headers).toEqual({
      Authorization: 'Bearer private-access-token',
    });
  });
  it('requests a separate client token for public API checks without any user ownership claim', async () => {
    const request = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        Response.json({ access_token: 'service-token', token_type: 'Bearer', expires_in: 86400 }),
      );
    const result = await battleNetClientToken(bindings(), request);
    expect(result.accessToken).toBe('service-token');
    expect(result.expiresAt).toBeGreaterThan(Date.now());
    expect(request.mock.calls[0][0]).toBe('https://oauth.battle.net/token');
    const init = request.mock.calls[0][1]!;
    expect((init.body as URLSearchParams).toString()).toBe('grant_type=client_credentials');
    expect(init.headers).toMatchObject({ Authorization: `Basic ${btoa('bn-client:bn-secret')}` });
    await expect(battleNetClientToken({}, request)).rejects.toMatchObject({
      code: 'credentials_required',
    });
  });
  it('rejects revoked credentials, HTTP 200 errors and malformed subject claims', async () => {
    const request = vi.fn<typeof fetch>().mockResolvedValueOnce(new Response('', { status: 401 }));
    await expect(
      completeGameOAuth(state('battlenet'), 'code', bindings(), request),
    ).rejects.toMatchObject({ code: 'authorization_revoked' });
    request.mockResolvedValueOnce(Response.json({ error: 'invalid_grant' }));
    await expect(
      completeGameOAuth(state('battlenet'), 'code', bindings(), request),
    ).rejects.toMatchObject({ code: 'invalid_code' });
    request
      .mockResolvedValueOnce(Response.json({ access_token: 'token' }))
      .mockResolvedValueOnce(Response.json({ battletag: 'Name#1234' }));
    await expect(
      completeGameOAuth(state('battlenet'), 'code', bindings(), request),
    ).rejects.toMatchObject({ code: 'invalid_response' });
  });
});

describe('Durable verification state and authenticated callback routing', () => {
  it('atomically consumes a state once and binds it to the initiating Discord session', async () => {
    const store = env.AUTH.getByName(`game-oauth:${nonce}`);
    await store.createGameVerification(state());
    expect(await store.consumeGameVerification('wrong-session', userId)).toBeNull();
    expect(await store.consumeGameVerification(sessionId, 'another-user')).toBeNull();
    const results = await Promise.all([
      store.consumeGameVerification(sessionId, userId),
      store.consumeGameVerification(sessionId, userId),
    ]);
    expect(results.filter(Boolean)).toHaveLength(1);
  });
  it('rejects expired authorization and expired character selection', async () => {
    const store = env.AUTH.getByName(`game-oauth:${nonce}`);
    await store.createGameVerification({ ...state(), expiresAt: Date.now() - 1 });
    expect(await store.consumeGameVerification(sessionId, userId)).toBeNull();
    const choice = env.AUTH.getByName(`game-choice:${nonce}`);
    await choice.createGameChoices({
      ...state('battlenet'),
      expiresAt: Date.now() - 1,
      accountId: '123',
      profiles: [],
      unavailableRegions: [],
    });
    expect(await choice.consumeGameChoices(sessionId, userId)).toBeNull();
  });
  it('requires login, same-origin CSRF and provider/game compatibility to start', async () => {
    const app = router();
    const request = (headers: Record<string, string> = {}) =>
      app.request(
        `${origin}/auth/game/start`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Origin: origin,
            'X-CSRF-Token': csrf,
            ...headers,
          },
          body: JSON.stringify({ communityId: guild, gameId: game, provider: 'steam' }),
        },
        bindings(),
      );
    signedIn = false;
    expect((await request()).status).toBe(401);
    signedIn = true;
    expect((await request({ Origin: 'https://evil.example' })).status).toBe(403);
    expect((await request({ 'X-CSRF-Token': 'wrong' })).status).toBe(403);
    catalog = 'sc2';
    expect((await request()).status).toBe(400);
    expect(link).not.toHaveBeenCalled();
  });
  it('starts Steam verification for a first-time player without any participation write', async () => {
    const response = await router().request(
      `${origin}/auth/game/start`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Origin: origin, 'X-CSRF-Token': csrf },
        body: JSON.stringify({ communityId: guild, gameId: game, provider: 'steam' }),
      },
      bindings(),
    );
    expect(response.status).toBe(200);
    const result = (await response.json()) as { url: string };
    const returnTo = new URL(result.url).searchParams.get('openid.return_to')!;
    const savedNonce = new URL(returnTo).searchParams.get('state')!;
    const store = env.AUTH.getByName(`game-oauth:${savedNonce}`);
    expect(await store.consumeGameVerification(sessionId, userId)).toMatchObject({
      communityId: guild,
      gameId: game,
      discordUserId: userId,
      catalogId: 'northgard',
    });
    expect(authorize).toHaveBeenCalledTimes(1);
    expect(link).not.toHaveBeenCalled();
  });
  it('links Steam ownership only after valid callback and never reuses its state', async () => {
    await env.AUTH.getByName(`game-oauth:${nonce}`).createGameVerification(state());
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(steamSuccess());
    const app = router();
    otherSession = true;
    const callback = `${origin}/auth/game/callback/steam?${assertion()}`;
    expect((await app.request(callback, {}, bindings())).status).toBe(400);
    expect(link).not.toHaveBeenCalled();
    otherSession = false;
    const success = await app.request(callback, {}, bindings());
    expect(success.status).toBe(200);
    expect(success.headers.get('Referrer-Policy')).toBe('no-referrer');
    expect(link).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({
        provider: 'steam',
        externalId: '76561198000000001',
        verifiedBy: 'steam-openid',
        discordUserId: userId,
      }),
    );
    expect((await app.request(callback, {}, bindings())).status).toBe(400);
    expect(link).toHaveBeenCalledTimes(1);
  });
  it('does not complete a callback after membership or configured game changes', async () => {
    await env.AUTH.getByName(`game-oauth:${nonce}`).createGameVerification(state());
    catalog = 'sc2';
    const response = await router().request(
      `${origin}/auth/game/callback/steam?${assertion()}`,
      {},
      bindings(),
    );
    expect(response.status).toBe(400);
    expect(link).not.toHaveBeenCalled();
  });
  it('keeps duplicate/reassigned identity rejection in the authoritative store', async () => {
    await env.AUTH.getByName(`game-oauth:${nonce}`).createGameVerification(state());
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(steamSuccess());
    link.mockResolvedValue({
      ok: false,
      content: 'That account is already assigned. An organizer must explicitly reassign it.',
    });
    const response = await router().request(
      `${origin}/auth/game/callback/steam?${assertion()}`,
      {},
      bindings(),
    );
    expect(response.status).toBe(409);
    expect(await response.text()).toContain('An organizer must explicitly reassign');
  });
  it('requires configured OAuth credentials without claiming a successful verification', async () => {
    catalog = 'sc2';
    const noCredentials = {
      ...bindings(),
      BATTLENET_CLIENT_ID: undefined,
      BATTLENET_CLIENT_SECRET: undefined,
    };
    const response = await router().request(
      `${origin}/auth/game/start`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Origin: origin, 'X-CSRF-Token': csrf },
        body: JSON.stringify({ communityId: guild, gameId: game, provider: 'battlenet' }),
      },
      noCredentials,
    );
    expect(response.status).toBe(503);
    expect(await response.json()).toMatchObject({ code: 'credentials_required' });
    expect(link).not.toHaveBeenCalled();
  });
  it('lists only owned SC2 characters, stores no token, and verifies the selected regional tuple', async () => {
    catalog = 'sc2';
    await env.AUTH.getByName(`game-oauth:${nonce}`).createGameVerification(state('battlenet'));
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (url) => {
      const value = String(url);
      if (value.endsWith('/token'))
        return Response.json({ access_token: 'never-persist-this', token_type: 'Bearer' });
      if (value.endsWith('/userinfo'))
        return Response.json({ sub: '777', battletag: 'Player#1234' });
      if (value.includes('eu.api'))
        return Response.json([{ profileId: '999', realmId: 2, regionId: 2, name: 'EU character' }]);
      return Response.json([{ profileId: '123', realmId: 1, regionId: 1, name: '<US character>' }]);
    });
    const app = router();
    const response = await app.request(
      `${origin}/auth/game/callback/battlenet?state=${nonce}&code=code`,
      {},
      bindings(),
    );
    expect(response.status).toBe(200);
    const html = await response.text();
    expect(html).toContain('&lt;US character&gt;');
    expect(html).toContain('2:2:999');
    expect(html).not.toContain('never-persist-this');
    expect(link).not.toHaveBeenCalled();
    const choiceNonce = /name="state" value="([a-f0-9]{64})"/.exec(html)![1];
    const choiceStore = env.AUTH.getByName(`game-choice:${choiceNonce}`);
    await runInDurableObject(choiceStore, async (_instance, durable) => {
      expect(JSON.stringify(await durable.storage.get('game-choices'))).not.toContain(
        'never-persist-this',
      );
    });
    const selected = await app.request(
      `${origin}/auth/game/select`,
      {
        method: 'POST',
        headers: { Origin: origin, 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({ state: choiceNonce, csrf, profileId: '2:2:999' }),
      },
      bindings(),
    );
    expect(selected.status).toBe(200);
    expect(link).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({
        provider: 'battle-net',
        externalId: '2:2:999',
        platformId: '777',
        verifiedBy: 'battlenet-oauth',
      }),
    );
  });
  it('rejects selecting another player’s character or a stale selection', async () => {
    catalog = 'sc2';
    await env.AUTH.getByName(`game-choice:${nonce}`).createGameChoices({
      ...state('battlenet'),
      accountId: '777',
      profiles: [
        {
          id: '1:1:123',
          name: 'Mine',
          url: 'https://starcraft2.blizzard.com/en-us/profile/1/1/123',
        },
      ],
      unavailableRegions: [],
    });
    const app = router();
    const choose = (id: string) =>
      app.request(
        `${origin}/auth/game/select`,
        {
          method: 'POST',
          headers: { Origin: origin, 'Content-Type': 'application/x-www-form-urlencoded' },
          body: new URLSearchParams({ state: nonce, csrf, profileId: id }),
        },
        bindings(),
      );
    expect((await choose('2:2:999')).status).toBe(400);
    expect(link).not.toHaveBeenCalled();
    expect((await choose('1:1:123')).status).toBe(400);
  });
});

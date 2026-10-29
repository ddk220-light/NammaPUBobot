import { env } from 'cloudflare:workers';
import { reset, runInDurableObject } from 'cloudflare:test';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { connectorAvailability, requestOperation, runtimeAdapter } from '../src/games/runtime';
import { gameAdapter } from '../src/games/registry';
import type { Env } from '../src/env';
import type { ProviderCoordinator } from '../src/storage/provider-coordinator';

const credentials = { BATTLENET_CLIENT_ID: 'test-client', BATTLENET_CLIENT_SECRET: 'test-secret' };
afterEach(async () => {
  vi.restoreAllMocks();
  await reset();
});

async function coordinator<T>(
  name: string,
  run: (instance: ProviderCoordinator, state: DurableObjectState) => Promise<T>,
) {
  return runInDurableObject(env.PROVIDERS!.getByName(name), async (instance, state) => {
    // Supply test-only bindings to the actual Durable Object instance; never persist credentials.
    const scope = instance as unknown as { env: Env };
    const previous = scope.env;
    scope.env = { ...previous, ...credentials };
    try {
      return await run(instance, state);
    } finally {
      scope.env = previous;
    }
  });
}

describe('application capability gates before network reads', () => {
  it('disables a provider and individual account lookup before public reads or Steam crosswalks', async () => {
    const request = vi.spyOn(globalThis, 'fetch');
    const all = runtimeAdapter({ ...env, CONNECTOR_DISABLED: 'aoe2' }, 'aoe2')!;
    expect(await all.profile('123')).toEqual({ status: 'unsupported' });
    expect(await all.observe('123', false)).toEqual({ status: 'unsupported' });
    const account = runtimeAdapter({ ...env, CONNECTOR_DISABLED: 'aoe3.accountLookup' }, 'aoe3')!;
    expect(await account.profile('123')).toEqual({ status: 'unsupported' });
    expect(await account.resolveSteam!('76561198000000001')).toEqual({ status: 'unsupported' });
    expect(request).not.toHaveBeenCalled();
  });
  it('does not request a Battle.net service token for a disabled lookup/history capability', async () => {
    const request = vi.spyOn(globalThis, 'fetch');
    const adapter = runtimeAdapter(
      { ...env, ...credentials, CONNECTOR_DISABLED: 'sc2.accountLookup,sc2.historyLookup' },
      'sc2',
    )!;
    expect(await adapter.profile('1:1:123')).toEqual({ status: 'unsupported' });
    expect(await adapter.supportingHistory!('1:1:123')).toEqual({ status: 'unsupported' });
    expect(request).not.toHaveBeenCalled();
  });
  it('keeps unaccepted observations gated while allowing bounded independent account reads', async () => {
    const request = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response(null, { status: 404 }));
    const adapter = runtimeAdapter({ ...env, PROVIDERS: undefined }, 'aoe4')!;
    expect(await adapter.observe('123', false)).toEqual({ status: 'unsupported' });
    expect(await adapter.discover!()).toEqual({ status: 'unsupported' });
    expect(request).not.toHaveBeenCalled();
    expect((await adapter.profile('123')).status).toBe('not_found');
    expect(request).toHaveBeenCalledTimes(1);
  });
  it('can disable the Steam authentication provider without disabling public Age lookups', () => {
    const bindings = { ...env, CONNECTOR_DISABLED: 'steam' };
    expect(connectorAvailability(bindings, gameAdapter('aoe3')!, 'ownershipVerification')).toBe(
      'disabled',
    );
    expect(connectorAvailability(bindings, gameAdapter('aoe3')!, 'accountLookup')).toBe(
      'available',
    );
  });
  it('checks provider-internal dependency reads against per-game capability switches', async () => {
    const request = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(Response.json({ result: { code: 0 }, matches: [], avatars: [] }));
    const accepted = { ...env, PROVIDERS: undefined, CONNECTOR_ACCEPTED_GAMES: 'aoe3,bar,aoe2' };
    const noDiscovery = runtimeAdapter(accepted, 'aoe3', { lobbyDiscovery: false })!;
    expect((await noDiscovery.observe('123', false)).status).toBe('unsupported');
    expect(request).not.toHaveBeenCalled();
    const noMatch = runtimeAdapter(accepted, 'aoe3', { matchLookup: false })!;
    expect(
      (
        await noMatch.observe('123', true, {
          nativeMatchId: '456',
          profileIds: ['10', '20'],
          attachedAt: Date.now(),
        })
      ).status,
    ).toBe('unsupported');
    expect(request).not.toHaveBeenCalled();
    const noBarDiscovery = runtimeAdapter(accepted, 'bar', { lobbyDiscovery: false })!;
    expect((await noBarDiscovery.profile('123')).status).toBe('unsupported');
    expect(request).not.toHaveBeenCalled();
    const noAge2Match = runtimeAdapter(accepted, 'aoe2', { matchLookup: false })!;
    expect((await noAge2Match.observe('123', true)).status).toBe('unsupported');
    expect(request).not.toHaveBeenCalled();
  });
  it('blocks a disabled internal history reconciliation after an allowed direct-match miss', async () => {
    const paths: string[] = [];
    const request = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const r = new Request(input, init),
        path = new URL(r.url).pathname;
      paths.push(path);
      return path.endsWith('/findAdvertisements')
        ? Response.json({ result: { code: 0 }, matches: [], avatars: [] })
        : Response.json({ result: { code: 2 } });
    });
    const adapter = runtimeAdapter(
      { ...env, PROVIDERS: undefined, CONNECTOR_ACCEPTED_GAMES: 'aoe3' },
      'aoe3',
      { historyLookup: false },
    )!;
    expect(
      (await adapter.observe('123', false, { profileIds: ['10', '20'], attachedAt: Date.now() }))
        .status,
    ).toBe('unsupported');
    expect(request).toHaveBeenCalledTimes(2);
    expect(paths.some((p) => p.endsWith('/getRecentMatchHistory'))).toBe(false);
  });
  it('keeps direct match lookup independent from the observer and removes disabled replay references', async () => {
    const now = Date.now();
    const request = vi.spyOn(globalThis, 'fetch').mockImplementation(async () =>
      Response.json({
        matchId: 123,
        started: new Date(now - 120000).toISOString(),
        finished: new Date(now - 60000).toISOString(),
        teams: [
          { teamId: 1, players: [{ profileId: 10, name: 'One', won: true, replay: true }] },
          { teamId: 2, players: [{ profileId: 20, name: 'Two', won: false, replay: false }] },
        ],
      }),
    );
    const adapter = runtimeAdapter({ ...env, PROVIDERS: undefined }, 'aoe2', {
      lobbyObservation: false,
      replayReferences: false,
    })!;
    expect(await adapter.observe('123', false)).toEqual({ status: 'unsupported' });
    const result = await adapter.match!('123');
    expect(result.status).toBe('ok');
    if (result.status === 'ok') expect(result.value.replayReferences).toBeUndefined();
    expect(request).toHaveBeenCalledTimes(1);
    expect(await adapter.replays!('123')).toEqual({ status: 'unsupported' });
    expect(request).toHaveBeenCalledTimes(1);
  });
  it.each([
    [
      'https://aoe-api.worldsedgelink.com/community/leaderboard/getReplayFiles?title=age3',
      'replayReferences',
    ],
    ['https://api.bar-rts.com/replays?page=1', 'historyLookup'],
    ['https://api.playgenerals.online/env/prod/contract/1/Lobbies', 'lobbyDiscovery'],
    ['https://eu.api.blizzard.com/sc2/legacy/profile/2/1/123/matches', 'historyLookup'],
    ['https://data.aoe2companion.com/api/matches?profile_ids=123', 'historyLookup'],
    ['https://example.com/unknown', undefined],
  ])('maps network route %s to its actual capability', (url, operation) => {
    expect(requestOperation(url!)).toBe(operation);
  });
  it.each([
    [401, 'authentication_required'],
    [503, 'unavailable'],
    [429, 'throttled'],
  ] as const)('preserves token HTTP %i as %s through the runtime wrapper', async (code, status) => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const request = new Request(input, init);
      expect(request.redirect).toBe('manual');
      expect(request.url).toBe('https://oauth.battle.net/token');
      return new Response(null, { status: code, headers: { 'Retry-After': '45' } });
    });
    const adapter = runtimeAdapter({ ...env, ...credentials, PROVIDERS: undefined }, 'sc2')!;
    const result = await adapter.profile('1:1:123');
    expect(result.status).toBe(status);
    if (result.status !== 'ok' && code !== 401) expect(result.retryAfterMs).toBe(45000);
  });
  it('rejects redirected token requests and malformed token bodies without calling a game API', async () => {
    const request = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      new Request(input, init); // Exercise Workerd's actual RequestInit support.
      return new Response(null, {
        status: 302,
        headers: { Location: 'https://attacker.example/token' },
      });
    });
    const adapter = runtimeAdapter({ ...env, ...credentials, PROVIDERS: undefined }, 'sc2')!;
    expect(await adapter.profile('1:1:123')).toEqual({ status: 'unavailable' });
    expect(request).toHaveBeenCalledTimes(1);
    request.mockResolvedValue(Response.json({ access_token: 'token', expires_in: -1 }));
    expect(await adapter.profile('1:1:123')).toEqual({ status: 'invalid_response' });
  });
  it.each([
    ['invalid_client', 'authentication_required'],
    ['invalid_grant', 'authentication_required'],
    ['temporarily_unavailable', 'unavailable'],
    ['rate_limit_exceeded', 'throttled'],
  ])('classifies a valid OAuth error inside HTTP 200 (%s)', async (error, status) => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(Response.json({ error }));
    const result = await runtimeAdapter(
      { ...env, ...credentials, PROVIDERS: undefined },
      'sc2',
    )!.profile('1:1:123');
    expect(result.status).toBe(status);
  });
});

describe('shared service authorization and diagnostics', () => {
  it('coalesces token grants, keeps the bearer out of storage, and invalidates only the rejected generation', async () => {
    let issued = 0;
    const request = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const r = new Request(input, init);
      expect(r.url).toBe('https://oauth.battle.net/token');
      return Response.json({ access_token: `service-token-${++issued}`, expires_in: 3600 });
    });
    await coordinator('auth-test-cache', async (instance, state) => {
      const [a, b] = await Promise.all([instance.battleNetToken(), instance.battleNetToken()]);
      expect(a).toEqual(b);
      expect(request).toHaveBeenCalledTimes(1);
      expect(JSON.stringify(await state.storage.get('diagnostics'))).not.toContain('service-token');
      await instance.invalidateBattleNetToken('wrong-old-token');
      expect(await instance.battleNetToken()).toEqual(a);
      expect(request).toHaveBeenCalledTimes(1);
      await instance.invalidateBattleNetToken('service-token-1');
      expect(await instance.battleNetToken()).toMatchObject({
        status: 'ok',
        value: { accessToken: 'service-token-2' },
      });
      expect(request).toHaveBeenCalledTimes(2);
      await instance.invalidateBattleNetToken('service-token-1');
      expect(await instance.battleNetToken()).toMatchObject({
        status: 'ok',
        value: { accessToken: 'service-token-2' },
      });
    });
  });
  it.each([
    [401, 'authentication_required', 300000],
    [503, 'unavailable', 90000],
    [429, 'throttled', 90000],
  ] as const)(
    'backs off token HTTP %i without recategorizing it as success or retrying every community call',
    async (code, status, expectedDelay) => {
      const request = vi
        .spyOn(globalThis, 'fetch')
        .mockImplementation(
          async () => new Response(null, { status: code, headers: { 'Retry-After': '90' } }),
        );
      await coordinator(`auth-test-${code}`, async (instance) => {
        const first = await instance.battleNetToken();
        expect(first.status).toBe(status);
        if (first.status !== 'ok')
          expect(first.retryAfterMs).toBeGreaterThanOrEqual(expectedDelay - 1000);
        expect((await instance.battleNetToken()).status).toBe(status);
        expect(request).toHaveBeenCalledTimes(1);
        const diagnostics = await instance.status();
        expect(diagnostics).toMatchObject({
          failures: 1,
          totalFailures: 1,
          transportFailures: 1,
          lastFailure: status,
        });
        expect(diagnostics.lastSuccessAt).toBeUndefined();
      });
    },
  );
  it('counts normalized schema failures once and only records success after decoding', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) =>
      String(input).endsWith('/124')
        ? Response.json({ profileId: 124, name: 'Valid profile' })
        : Response.json({ unrelated: true }),
    );
    const adapter = runtimeAdapter(env, 'aoe2')!;
    expect((await adapter.profile('123')).status).toBe('invalid_response');
    expect((await adapter.profile('123')).status).toBe('invalid_response');
    const coordinator = env.PROVIDERS!.getByName('data.aoe2companion.com');
    expect(await coordinator.status()).toMatchObject({
      failures: 2,
      totalFailures: 2,
      invalidResponses: 2,
      transportFailures: 0,
    });
    expect((await coordinator.status()).lastSuccessAt).toBeUndefined();
    expect((await adapter.profile('124')).status).toBe('ok');
    expect(await coordinator.status()).toMatchObject({
      failures: 0,
      totalFailures: 2,
      invalidResponses: 2,
    });
    expect((await coordinator.status()).lastSuccessAt).toBeGreaterThan(0);
  });
  it('records one failed operation for one HTTP failure, separate from transport counters', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(null, { status: 429, headers: { 'Retry-After': '60' } }),
    );
    expect((await runtimeAdapter(env, 'aoe2')!.profile('123')).status).toBe('throttled');
    expect(await env.PROVIDERS!.getByName('data.aoe2companion.com').status()).toMatchObject({
      failures: 1,
      totalFailures: 1,
      transportFailures: 1,
      throttled: 1,
      throttledOperations: 1,
    });
  });
  it('does not turn an offline BAR account retry into a host-wide outage for other operations', async () => {
    const request = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => Response.json([]));
    expect(await runtimeAdapter(env, 'bar')!.profile('123')).toEqual({
      status: 'delayed',
      retryAfterMs: 120000,
    });
    const host = env.PROVIDERS!.getByName('api.bar-rts.com');
    expect((await host.status()).retryAt).toBe(0);
    expect(await host.request({ url: 'https://api.bar-rts.com/replays?page=1&limit=1' })).toEqual({
      status: 'ok',
      value: [],
    });
    expect(request).toHaveBeenCalledTimes(2);
    await host.recordOutcome('not_found', 120000);
    expect((await host.status()).retryAt).toBe(0);
  });
});

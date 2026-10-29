import aoe2Fixture from './fixtures/connectors/aoe2-v1.json';
import { env } from 'cloudflare:workers';
import { reset } from 'cloudflare:test';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { isSteamIndividualId } from '../src/games/steam';
import { gameCatalog } from '../src/games/catalog';
import { gameAdapter } from '../src/games/registry';
import { connectorAvailability } from '../src/games/runtime';
import { parseProviderJson, providerJson } from '../src/games/transport';
import { parseAoE2Match } from '../src/games/aoe2';
import {
  validObservation,
  normalizeObservation,
  suggestedResult,
  type LobbyBinding,
} from '../src/domain/lobby';
import type { ProviderFailure } from '../src/games/types';

afterEach(async () => {
  await reset();
  vi.restoreAllMocks();
});
const now = Date.parse('2026-10-28T19:00:00Z');
const match = {
  matchId: 9001,
  name: 'Fixture',
  started: '2026-10-28T18:32:02Z',
  finished: '2026-10-28T18:52:01Z',
  teams: [
    {
      teamId: 1,
      players: [
        {
          profileId: 101,
          name: 'One',
          civName: 'Future civilization',
          civ: 999,
          won: true,
          replay: true,
        },
      ],
    },
    { teamId: 2, players: [{ profileId: 102, name: 'Two', won: false, replay: false }] },
  ],
};

describe('shared provider contract', () => {
  it.each(gameCatalog.map((g) => [g.id] as const))(
    '%s declares every operation and keeps manual association available',
    (id) => {
      const adapter = gameAdapter(id)!;
      expect(adapter.manifest).toEqual(gameCatalog.find((g) => g.id === id)!.manifest);
      expect(adapter.parseLobbyReference('9001')).toBe('9001');
      for (const operation of Object.keys(adapter.manifest.capabilities))
        expect(['available', 'unsupported', 'credentials_required']).toContain(
          adapter.manifest.capabilities[operation as keyof typeof adapter.manifest.capabilities],
        );
    },
  );
  // The same failure cases run against every network observer, independently of provider fixtures.
  for (const id of ['aoe2', 'aoe3', 'aoe4', 'aom', 'bar', 'zero_hour']) {
    it.each<ProviderFailure>([
      'throttled',
      'unavailable',
      'authentication_required',
      'invalid_response',
    ])(`${id} preserves a failed read (%s) instead of an empty success`, async (status) => {
      const adapter = gameAdapter(id, {
        generalsOnlineApiKey: 'fixture-key',
        request: async () => ({ status, retryAfterMs: 12345 }),
      })!;
      const result = await adapter.observe('9001', false, {
        attachedAt: now,
        profileIds: ['101', '102'],
      });
      expect(result.status).not.toBe('ok');
      expect([
        'not_found',
        'delayed',
        'unavailable',
        'throttled',
        'authentication_required',
        'unsupported',
        'invalid_response',
      ]).toContain(result.status);
      if (result.status === 'throttled') expect(result.retryAfterMs).toBe(12345);
    });
  }
  it('new automatic paths require acceptance independently of community toggles and credentials', () => {
    expect(connectorAvailability(env, gameAdapter('aoe4')!, 'lobbyObservation')).toBe(
      'acceptance_required',
    );
    expect(
      connectorAvailability(
        { ...env, CONNECTOR_ACCEPTED_GAMES: 'aoe4' },
        gameAdapter('aoe4')!,
        'lobbyObservation',
      ),
    ).toBe('available');
    expect(
      connectorAvailability(
        { ...env, CONNECTOR_ACCEPTED_GAMES: 'aoe4', CONNECTOR_DISABLED: 'aoe4.lobbyObservation' },
        gameAdapter('aoe4')!,
        'lobbyObservation',
      ),
    ).toBe('disabled');
    expect(connectorAvailability(env, gameAdapter('zero_hour')!, 'lobbyObservation')).toBe(
      'credentials_required',
    );
    expect(connectorAvailability(env, gameAdapter('sc2')!, 'lobbyObservation')).toBe('unsupported');
  });
});

describe('bounded lossless transport', () => {
  it('preserves the full Steam individual account range including newer 7656120 prefixes', async () => {
    for (const id of ['76561197960265728', '76561200000000000', '76561202255233023']) {
      expect(isSteamIndividualId(id)).toBe(true);
      expect(gameAdapter('northgard')!.parseAccountReference!(id)).toBe(id);
      const found = await gameAdapter('aoe2', {
        request: async () => ({
          status: 'ok',
          value: { profileId: 101, name: 'Fixture', steamId: id },
        }),
      })!.profile('101');
      expect(found).toMatchObject({ status: 'ok', value: { platformId: id } });
    }
    for (const title of ['aoe3', 'aoe4', 'aom']) {
      const id = '76561200000000000';
      const adapter = gameAdapter(title, {
        request: async () => ({
          status: 'ok',
          value: {
            result: { code: 0, message: 'SUCCESS' },
            statGroups: [
              { members: [{ profile_id: 101, alias: 'Fixture', name: `/steam/${id}` }] },
            ],
          },
        }),
      })!;
      expect(await adapter.resolveSteam!(id)).toMatchObject({
        status: 'ok',
        value: { id: '101', platformId: id },
      });
    }
    expect(isSteamIndividualId('76561197960265727')).toBe(false);
    expect(isSteamIndividualId('76561202255233024')).toBe(false);
    expect(isSteamIndividualId(76561200000000000)).toBe(false);
  });
  it('preserves oversized integer tokens without changing strings, escapes or safe numbers', () => {
    expect(
      parseProviderJson(
        '{"id":76561198449406083,"safe":42,"negative":-9007199254740993,"text":"id 76561198449406083 \\\"x\\\"","n":1.25}',
      ),
    ).toEqual({
      id: '76561198449406083',
      safe: 42,
      negative: '-9007199254740993',
      text: 'id 76561198449406083 "x"',
      n: 1.25,
    });
    expect(() => parseProviderJson('{"id":1e100}')).toThrow();
    expect(() => parseProviderJson('{"id":01}')).toThrow();
  });
  it('refuses arbitrary hosts, redirects and credential-bearing URLs before any request', async () => {
    const request = vi.spyOn(globalThis, 'fetch');
    for (const url of [
      'https://example.com/data',
      'http://aoe4world.com/api',
      'https://secret@aoe4world.com/api',
      'https://aoe4world.com:444/api',
    ])
      expect(await providerJson({ url })).toEqual({ status: 'invalid_response' });
    expect(request).not.toHaveBeenCalled();
  });
  it.each([
    [404, 'not_found'],
    [401, 'authentication_required'],
    [403, 'authentication_required'],
    [429, 'throttled'],
    [500, 'unavailable'],
  ] as const)('normalizes HTTP %i as %s', async (code, status) => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(null, { status: code, headers: { 'Retry-After': '12' } }),
    );
    const result = await providerJson({ url: 'https://aoe4world.com/api/v0/players/1' });
    expect(result.status).toBe(status);
    if (result.status !== 'ok') expect(result.retryAfterMs).toBe(12000);
  });
  it('distinguishes malformed JSON from unavailable transport and limits streaming bodies', async () => {
    const request = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{bad'));
    expect(await providerJson({ url: 'https://aoe4world.com/api' })).toEqual({
      status: 'invalid_response',
    });
    request.mockResolvedValue(new Response(JSON.stringify({ data: 'x'.repeat(2000) })));
    expect(await providerJson({ url: 'https://aoe4world.com/api', maxBytes: 1024 })).toEqual({
      status: 'invalid_response',
    });
    request.mockRejectedValue(new Error('offline'));
    expect(await providerJson({ url: 'https://aoe4world.com/api' })).toEqual({
      status: 'unavailable',
    });
  });
  it('uses Worker-supported redirect handling without following provider redirects', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const request = new Request(input, init); // Exercise Workerd's actual Request option validation.
      expect(request.redirect).toBe('manual');
      return new Response(null, {
        status: 302,
        headers: { Location: 'https://untrusted.example/' },
      });
    });
    expect(await providerJson({ url: 'https://aoe4world.com/api' })).toEqual({
      status: 'unavailable',
    });
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it('handles a current-size AoE2 profile without claiming ownership', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      Response.json({
        profileId: 199325,
        name: 'Fixture',
        steamId: '76561198449406083',
        ratings: 'x'.repeat(700000),
      }),
    );
    expect(await gameAdapter('aoe2')!.profile('199325')).toMatchObject({
      status: 'ok',
      value: { id: '199325', platformId: '76561198449406083' },
    });
  });
  it('coalesces public reads across callers, tracks failures, and honors shared Retry-After', async () => {
    const request = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(async () => Response.json({ ok: true }));
    const coordinator = env.PROVIDERS!.getByName('contract-test');
    const input = { url: 'https://aoe4world.com/api/v0/players/1', cacheTtlMs: 30000 };
    const [a, b] = await Promise.all([coordinator.request(input), coordinator.request(input)]);
    expect(a).toEqual(b);
    expect(request).toHaveBeenCalledTimes(1);
    expect(await coordinator.request(input)).toEqual(a);
    expect(request).toHaveBeenCalledTimes(1);
    request.mockResolvedValue(new Response(null, { status: 429, headers: { 'Retry-After': '2' } }));
    expect(
      (await coordinator.request({ url: 'https://aoe4world.com/api/v0/players/2' })).status,
    ).toBe('throttled');
    const count = request.mock.calls.length;
    expect(
      (await coordinator.request({ url: 'https://aoe4world.com/api/v0/players/3' })).status,
    ).toBe('throttled');
    expect(request).toHaveBeenCalledTimes(count);
    await coordinator.recordOutcome('invalid_response');
    expect(await coordinator.status()).toMatchObject({
      throttled: 1,
      invalidResponses: 1,
      lastFailure: 'invalid_response',
    });
  });
});

describe('evidence compatibility and result suggestions', () => {
  it('decodes the sanitized research capture without manufacturing unavailable recordings', () => {
    const o = parseAoE2Match(aoe2Fixture.match, '9002', now);
    expect(o.phase).toBe('finished');
    expect(o.elapsedDuration).toEqual({ value: 1199, unit: 'seconds' });
    expect(o.players.map((p) => p.selection).sort()).toEqual(['Vietnamese', 'Wei']);
    expect(o.replayReferences?.every((r) => r.availability === 'unavailable' && !r.sourceUrl)).toBe(
      true,
    );
  });

  it('retains elapsed time, winner evidence, unknown selections and conditional recording references', () => {
    const o = parseAoE2Match(match, '9001', now);
    expect(validObservation(o, now)).toBe(true);
    expect(o.elapsedDuration).toEqual({ value: 1199, unit: 'seconds' });
    expect(o.simulationDuration).toBeUndefined();
    expect(o.players[0]).toMatchObject({
      selection: 'Future civilization',
      selectionId: '999',
      outcome: 'win',
    });
    expect(o.replayReferences).toMatchObject([
      { availability: 'available', sourceUrl: 'https://aoe.ms/replay/?gameId=9001&profileId=101' },
      { availability: 'unavailable' },
    ]);
    const legacy = {
      externalId: '9001',
      name: 'Legacy',
      phase: 'open' as const,
      observedAt: now,
      startedAt: null,
      finishedAt: null,
      players: [],
      capacity: null,
      map: null,
      source: 'legacy',
    };
    expect(validObservation(legacy, now)).toBe(true);
    expect(normalizeObservation(legacy, 'generation')).toMatchObject({
      schemaVersion: 2,
      bindingGeneration: 'generation',
      nativeLobbyId: '9001',
      confidence: 'provisional',
    });
  });
  it('requires a complete matching roster and coherent two-team outcome before prefilling', () => {
    const o = parseAoE2Match(match, '9001', now);
    const binding = {
      profiles: [
        { userId: 'one', profileId: '101', side: 0 },
        { userId: 'two', profileId: '102', side: 1 },
      ],
    } as LobbyBinding;
    expect(suggestedResult(binding, o)).toBe('alpha');
    o.players[1].outcome = 'win';
    expect(suggestedResult(binding, o)).toBeUndefined();
    o.players[1].outcome = 'loss';
    o.players[1].profileId = 'other';
    expect(suggestedResult(binding, o)).toBeUndefined();
  });
});

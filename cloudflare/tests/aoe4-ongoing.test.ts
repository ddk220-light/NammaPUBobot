import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { aoe4Configuration, createAoE4Adapter, enrichAoE4Match } from '../src/games/aoe4';
import { decodeAoE4WorldGame } from '../src/games/aoe4-world';
import { decodeAgeMatch } from '../src/games/worlds-edge';
import type { JsonRequest, LobbyObservation, ProviderResult } from '../src/games/types';
import fixture from './fixtures/connectors/age-aoe4-world-v1.json';
import nativeFixture from './fixtures/connectors/age-aoe4-v1.json';

type Data = Record<string, any>;
const now = Date.parse('2026-10-29T00:20:00Z');
const reference = 'aoe4world:257846458';
function detail(): Data {
  const game = structuredClone(fixture.feed.games[0]) as Data;
  game.teams = game.teams.map((team: Data[]) => team.map((member) => member.player));
  return game;
}
function harness() {
  const bodies = { feed: structuredClone(fixture.feed) as Data, detail: detail() };
  const request = vi.fn(async (request: JsonRequest): Promise<ProviderResult<unknown>> => {
    const url = new URL(request.url);
    if (url.hostname !== 'aoe4world.com') throw Error('Unexpected native publisher request');
    return {
      status: 'ok',
      value: structuredClone(url.pathname === '/api/v0/games' ? bodies.feed : bodies.detail),
    };
  });
  return { adapter: createAoE4Adapter({ request }), request, bodies };
}
function observed(): LobbyObservation {
  const result = decodeAoE4WorldGame(fixture.feed.games[0], now);
  if (result.status !== 'ok') throw Error('Invalid fixture');
  return result.value;
}
function context() {
  const previous = observed();
  return {
    previous,
    profileIds: previous.players.map((p) => p.profileId),
    matchCreatedAt: previous.startedAt! - 300000,
    attachedAt: now,
  };
}
beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(now);
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('AoE4 World ongoing public discovery', () => {
  it('reads one bounded ranked 1v1 page and keeps provider match IDs separate', async () => {
    const { adapter, request } = harness();
    const result = await adapter.discoverOngoing!();
    expect(result.status).toBe('ok');
    if (result.status !== 'ok') return;
    expect(result.value).toHaveLength(2); // The captured third row has just finished.
    expect(result.value[0]).toMatchObject({
      externalId: reference,
      referenceKind: 'match',
      providerIds: { aoe4world: '257846458' },
      provider: 'aoe4world',
      phase: 'started',
      startedAt: Date.parse('2026-10-29T00:15:31.000Z'),
      finishedAt: null,
      map: 'The Pit',
      mode: 'rm_1v1',
      players: [
        { profileId: '25574841', selection: 'Macedonian Dynasty', team: 'aoe4world:0' },
        { profileId: '11230685', selection: 'Ottomans', team: 'aoe4world:1' },
      ],
    });
    for (const match of result.value) {
      expect(match.nativeLobbyId).toBeUndefined();
      expect(match.nativeMatchId).toBeUndefined();
      expect(match.elapsedDuration).toBeUndefined();
      expect(match.players.every((p) => p.outcome === undefined)).toBe(true);
    }
    expect(request).toHaveBeenCalledTimes(1); // Never chase the captured next_page=2.
    const sent = request.mock.calls[0][0],
      url = new URL(sent.url);
    expect(url.pathname).toBe('/api/v0/games');
    expect(Object.fromEntries(url.searchParams)).toEqual({
      order: 'started_at',
      since: String(Math.floor(now / 1000) - 1800),
      leaderboard: 'rm_1v1',
    });
    expect(sent.maxBytes).toBe(1000000);
    expect(sent.cacheTtlMs).toBeGreaterThan(0);
  });
  it('filters stale games and other modes even if the server ignores query filters', async () => {
    const { adapter, bodies } = harness();
    bodies.feed.games[0].started_at = new Date(now - 1801000).toISOString();
    bodies.feed.games[1].kind = 'qm_1v1';
    expect(await adapter.discoverOngoing!()).toEqual({ status: 'ok', value: [] });
  });
  it('preserves unfamiliar civilization slugs and never emits ongoing outcomes', async () => {
    const { adapter, bodies } = harness();
    const player = bodies.feed.games[0].teams[0][0].player;
    player.civilization = 'future_civilization';
    player.result = 'win';
    const result = await adapter.discoverOngoing!();
    expect(result.status === 'ok' && result.value[0].players[0]).toMatchObject({
      selectionId: 'aoe4world:future_civilization',
      selection: 'Future Civilization',
    });
    expect(result.status === 'ok' && result.value[0].players[0].outcome).toBeUndefined();
  });
  it.each([
    null,
    {},
    { games: {} },
    { games: [], error: 'Provider unavailable' },
    { games: [], result: { code: 9, message: 'ERROR' } },
    { games: Array(201).fill(fixture.feed.games[0]) },
  ])(
    'rejects malformed or application-error envelopes instead of empty success: %j',
    async (body) => {
      const adapter = createAoE4Adapter({ request: async () => ({ status: 'ok', value: body }) });
      expect(await adapter.discoverOngoing!()).toEqual({ status: 'invalid_response' });
    },
  );
  it.each(['duplicate game', 'duplicate player', 'unknown ongoing flag', 'future start'])(
    'rejects inconsistent discovery evidence: %s',
    async (failure) => {
      const { adapter, bodies } = harness();
      const game = bodies.feed.games[0];
      if (failure === 'duplicate game') bodies.feed.games[1].game_id = game.game_id;
      if (failure === 'duplicate player') game.teams[1][0].player = game.teams[0][0].player;
      if (failure === 'unknown ongoing flag') game.ongoing = 'true';
      if (failure === 'future start') game.started_at = new Date(now + 1000).toISOString();
      expect(await adapter.discoverOngoing!()).toEqual({ status: 'invalid_response' });
    },
  );
  it('propagates provider throttling with retry timing', async () => {
    const failure = { status: 'throttled' as const, retryAfterMs: 60000 };
    const adapter = createAoE4Adapter({ request: async () => failure });
    expect(await adapter.discoverOngoing!()).toEqual(failure);
  });
  it('enforces the byte limit when the public feed ignores requested page sizes', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(' '.repeat(1000001))),
    );
    expect(await createAoE4Adapter().discoverOngoing!()).toEqual({ status: 'invalid_response' });
  });
});

describe('explicit World references and observed-match continuity', () => {
  it('accepts explicit provider references and public match URLs without changing numeric native IDs', () => {
    const { adapter } = harness();
    expect(adapter.parseLobbyReference(reference)).toBe(reference);
    expect(
      adapter.parseLobbyReference('https://aoe4world.com/players/25574841/games/257846458'),
    ).toBe(reference);
    expect(adapter.parseLobbyReference('257846458')).toBe('257846458');
    for (const value of [
      'aoe4world:0',
      'http://aoe4world.com/players/1/games/257846458',
      'https://aoe4world.com.evil.test/players/1/games/257846458',
      'https://user:pass@aoe4world.com/players/1/games/257846458',
      'https://aoe4world.com/players/1/games/last',
    ])
      expect(() => adapter.parseLobbyReference(value)).toThrow();
  });
  it('observes the exact provider game without querying or inventing a native lobby', async () => {
    const { adapter, request } = harness();
    const result = await adapter.observe(reference, true, context());
    expect(result).toMatchObject({
      status: 'ok',
      value: { externalId: reference, confidence: 'roster', phase: 'started' },
    });
    if (result.status !== 'ok') return;
    expect(result.value.nativeLobbyId).toBeUndefined();
    expect(result.value.nativeMatchId).toBeUndefined();
    expect(request.mock.calls[0][0].url).toBe(
      'https://aoe4world.com/api/v0/players/11230685/games/257846458',
    );
  });
  it('permits a first explicit match attachment only with expected profiles and a time anchor', async () => {
    const { adapter, request } = harness();
    expect(await adapter.observe(reference, true)).toMatchObject({ status: 'delayed' });
    expect(request).not.toHaveBeenCalled();
    const { previous: _previous, ...initial } = context();
    expect(await adapter.observe(reference, true, initial)).toMatchObject({ status: 'ok' });
    expect(
      await adapter.observe(reference, true, { ...initial, matchCreatedAt: now }),
    ).toMatchObject({ status: 'invalid_response' });
  });
  it.each(['different game', 'different player', 'changed start', 'changed team partition'])(
    'rejects an exact lookup that breaks continuity: %s',
    async (failure) => {
      const { adapter, bodies } = harness();
      if (failure === 'different game') bodies.detail.game_id++;
      if (failure === 'different player') bodies.detail.teams[0][0].profile_id++;
      if (failure === 'changed start') bodies.detail.started_at = '2026-10-29T00:15:35.000Z';
      if (failure === 'changed team partition') bodies.detail.teams = [bodies.detail.teams.flat()];
      expect(await adapter.observe(reference, true, context())).toEqual({
        status: 'invalid_response',
      });
    },
  );
  it('accepts relabeled teams when the player partition stays the same', async () => {
    const { adapter, bodies } = harness();
    bodies.detail.teams.reverse();
    expect(await adapter.observe(reference, true, context())).toMatchObject({ status: 'ok' });
  });
  it('rejects native/provider binding conflicts before any network request', async () => {
    const { adapter, request } = harness();
    const ctx = context();
    ctx.previous.providerIds = { aoe4world: '123' };
    expect(await adapter.observe(reference, true, ctx)).toEqual({ status: 'invalid_response' });
    expect(request).not.toHaveBeenCalled();
  });
  it('keeps a just-finished game delayed until duration is available, then reports outcomes', async () => {
    const { adapter, bodies } = harness();
    bodies.detail.ongoing = false;
    bodies.detail.just_finished = true;
    bodies.detail.teams[0][0].result = 'win';
    bodies.detail.teams[1][0].result = 'loss';
    expect(await adapter.observe(reference, true, context())).toMatchObject({ status: 'delayed' });
    bodies.detail.duration = 120;
    expect(await adapter.observe(reference, true, context())).toMatchObject({
      status: 'ok',
      value: {
        externalId: reference,
        phase: 'finished',
        finishedAt: Date.parse('2026-10-29T00:17:31.000Z'),
        elapsedDuration: { value: 120, unit: 'seconds' },
        players: [{ outcome: 'win' }, { outcome: 'loss' }],
      },
    });
  });
  it('does not infer completion from a missing or private World game', async () => {
    const request = vi.fn(async (): Promise<ProviderResult<unknown>> => ({ status: 'not_found' }));
    const adapter = createAoE4Adapter({ request });
    expect(await adapter.observe(reference, true, context())).toMatchObject({ status: 'delayed' });
    request.mockResolvedValue({ status: 'authentication_required' });
    expect(await adapter.observe(reference, true, context())).toEqual({
      status: 'authentication_required',
    });
  });
});

describe('captured detail schema and publisher enrichment', () => {
  it('decodes flattened completed-game players and elapsed duration from the recorded response', () => {
    const result = decodeAoE4WorldGame(fixture.completedDetail, now);
    expect(result).toMatchObject({
      status: 'ok',
      value: {
        externalId: 'aoe4world:257612387',
        phase: 'finished',
        map: 'Dry Arabia',
        elapsedDuration: { value: 1630, unit: 'seconds' },
      },
    });
    expect(result.status === 'ok' && result.value.players).toHaveLength(6);
  });
  it('requires roster, team and start continuity before mapping matching numeric IDs', async () => {
    const native = decodeAgeMatch(
      nativeFixture.match.matchHistory[0],
      new Map(),
      aoe4Configuration,
      now,
    );
    const body = structuredClone(fixture.completedDetail) as Data;
    const runtime = {
      request: async (): Promise<ProviderResult<unknown>> => ({ status: 'ok', value: body }),
    };
    expect(await enrichAoE4Match(native, '4635035', runtime)).toMatchObject({
      status: 'ok',
      value: {
        nativeMatchId: '257612387',
        providerIds: { aoe4world: '257612387' },
        map: 'Dry Arabia',
      },
    });
    body.teams[0][0].profile_id++;
    expect(await enrichAoE4Match(native, '4635035', runtime)).toEqual({
      status: 'ok',
      value: native,
    });
  });
});

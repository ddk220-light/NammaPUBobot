import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import fixtures from './fixtures/connectors/wc3/v1/contracts.json';
import {
  createWc3Adapter,
  decodeWc3Match,
  decodeWc3Profile,
  parseWc3AccountReference,
  parseWc3MatchReference,
  wc3AccountNamespace,
  wc3ProfileUrl,
} from '../src/games/wc3';
import { wc3RaceSelection } from '../src/games/wc3-catalog';
import { parseProviderJson } from '../src/games/transport';
import type { JsonTransport, ProviderResult } from '../src/games/types';
import {
  normalizeObservation,
  rosterMatches,
  validObservation,
  type LobbyBinding,
} from '../src/domain/lobby';

const now = Date.parse('2026-10-29T02:00:00Z');
const ongoing = fixtures.playerOngoing;
const original = ongoing['original-ongoing-match-id'];
const playerId = fixtures.profile.battleTag;
const originalPath = `/api/matches/by-ongoing-match-id/${original}`;
const playerPath = `/api/matches/ongoing/${encodeURIComponent(playerId)}`;
type Row = Record<string, any>;

beforeEach(() => vi.spyOn(Date, 'now').mockReturnValue(now));
afterEach(() => vi.restoreAllMocks());

function harness() {
  const bodies = structuredClone(fixtures);
  const overrides = new Map<string, ProviderResult<unknown>>();
  const request = vi.fn<JsonTransport>().mockImplementation(async ({ url }) => {
    const path = new URL(url).pathname;
    if (overrides.has(path)) return overrides.get(path)!;
    if (path.startsWith('/api/players/')) return { status: 'ok', value: bodies.profile };
    if (path === '/api/matches/ongoing') return { status: 'ok', value: bodies.ongoing };
    if (path.startsWith('/api/matches/ongoing/'))
      return { status: 'ok', value: bodies.playerOngoing };
    if (path === '/api/matches/search') return { status: 'ok', value: bodies.history };
    for (const detail of [bodies.finished, bodies.correlatedFinished]) {
      if (
        path === `/api/matches/by-ongoing-match-id/${detail.match['original-ongoing-match-id']}` ||
        path === `/api/matches/${detail.match.id}`
      )
        return { status: 'ok', value: detail };
    }
    return { status: 'not_found' };
  });
  return { adapter: createWc3Adapter({ request }), bodies, overrides, request };
}

describe('W3Champions references and identities', () => {
  it('keeps the whole BattleTag and a separate provider namespace', () => {
    expect(parseWc3AccountReference('玩家#12345')).toBe('玩家#12345');
    expect(
      parseWc3AccountReference(
        `https://www.w3champions.com/player/${encodeURIComponent(playerId)}`,
      ),
    ).toBe(playerId);
    expect(wc3ProfileUrl(playerId)).toBe(
      `https://w3champions.com/player/${encodeURIComponent(playerId)}`,
    );
    expect(decodeWc3Profile(fixtures.profile, playerId)).toMatchObject({
      id: playerId,
      namespace: wc3AccountNamespace,
      name: fixtures.profile.name,
    });
    expect(decodeWc3Profile(fixtures.profile, playerId)).not.toHaveProperty('platformId');
    expect(() => decodeWc3Profile(fixtures.profile, 'Renamed#1234')).toThrow();
  });
  it.each([
    'Name',
    '12345',
    '*',
    'Name#12',
    'Name#1234/extra',
    'Na me#1234',
    'https://evil.test/player/Name%231234',
    'http://w3champions.com/player/Name%231234',
    'https://secret@w3champions.com/player/Name%231234',
    'https://w3champions.com:444/player/Name%231234',
    'https://w3champions.com/player/Name%231234?token=secret',
    'https://w3champions.com/player/Name#1234',
    'https://w3champions.com/player/Name%25231234',
    'https://w3champions.com/player/Name%231234%2Fextra',
  ])('rejects an unsafe or ambiguous account reference %s', (input) => {
    expect(() => parseWc3AccountReference(input)).toThrow();
  });
  it('keeps original IDs, record aliases and FLO IDs distinct', () => {
    expect(parseWc3MatchReference(original)).toBe(original);
    expect(parseWc3MatchReference(ongoing.id)).toBe(`record:${ongoing.id}`);
    expect(parseWc3MatchReference(`https://w3champions.com/match/${ongoing.id}`)).toBe(
      `record:${ongoing.id}`,
    );
    expect(parseWc3MatchReference(`record:${ongoing.id}`)).toBe(`record:${ongoing.id}`);
    for (const input of [
      'https://evil.test/match/' + ongoing.id,
      'https://w3champions.com/match/' + ongoing.id + '?x=1',
      'https://w3champions.com/match/' + ongoing.id + '#replay',
      'https://w3champions.com/match/' + original,
      'record:wrong',
      '../matches',
      'https://website-backend.w3champions.com/api/matches/' + ongoing.id,
    ])
      expect(() => parseWc3MatchReference(input)).toThrow();
  });
});

describe('W3Champions normalized match facts', () => {
  it('does not manufacture a lobby ID or result from ongoing placeholders', () => {
    const value = decodeWc3Match(ongoing, 'started', now);
    expect(value).toMatchObject({
      referenceKind: 'match',
      externalId: original,
      nativeMatchId: original,
      providerIds: {
        originalMatchId: original,
        recordId: ongoing.id,
        floGameId: String(ongoing.floGameId),
      },
      phase: 'started',
      finishedAt: null,
      mode: '1',
    });
    expect(value.players.every((p) => p.outcome === 'unknown')).toBe(true);
    expect(value).not.toHaveProperty('nativeLobbyId');
    expect(value).not.toHaveProperty('elapsedDuration');
    expect(value).not.toHaveProperty('simulationDuration');
    expect(normalizeObservation(value, 'generation')).not.toHaveProperty('nativeLobbyId');
    expect(validObservation(value, now)).toBe(true);
    const misleading = structuredClone(ongoing);
    misleading.teams[0].won = true;
    misleading.teams[0].players[0].won = true;
    expect(
      decodeWc3Match(misleading, 'started', now).players.every((p) => p.outcome === 'unknown'),
    ).toBe(true);
  });
  it('decodes captured 4v4 teams and distinguishes Random from its established race', () => {
    const live = decodeWc3Match(fixtures.teamOngoing.matches[0], 'started', now);
    const finished = decodeWc3Match(fixtures.teamFinished.matches[0], 'finished', now);
    expect(live.mode).toBe('4');
    expect(live.players.filter((p) => p.team === '1')).toHaveLength(4);
    expect(live.players.filter((p) => p.team === '2')).toHaveLength(4);
    expect(
      finished.players.some((p) => p.selectionId === '0' && p.selection === 'Random (Undead)'),
    ).toBe(true);
    expect(wc3RaceSelection(12345, null)).toEqual({
      selectionId: '12345',
      selection: 'Unknown race (12345)',
    });
    expect(wc3RaceSelection(0, 12345)).toEqual({
      selectionId: '0',
      selection: 'Random (Unknown race (12345))',
    });
    expect(finished.simulationDuration).toBeUndefined();
    expect(validObservation(finished, now)).toBe(true);
  });
  it('matches the shared two-team binding contract for captured 1v1 and 4v4 games', () => {
    for (const row of [ongoing, fixtures.teamOngoing.matches[0]]) {
      const observation = decodeWc3Match(row, 'started', now);
      const binding = {
        profiles: row.teams.flatMap((team, side) =>
          team.players.map((player) => ({
            userId: player.battleTag,
            profileId: player.battleTag,
            side,
          })),
        ),
      } as LobbyBinding;
      expect(rosterMatches(binding, observation)).toBe(true);
      expect(observation.players.every((p) => p.team === '1' || p.team === '2')).toBe(true);
    }
  });
  it.each(['both lose', 'both win', 'player conflicts', 'player outcome missing'])(
    'leaves outcomes unknown when %s',
    (kind) => {
      const row = structuredClone(fixtures.finished.match) as Row;
      if (kind === 'both lose')
        row.teams.forEach((t: Row) => {
          t.won = false;
          t.players.forEach((p: Row) => (p.won = false));
        });
      if (kind === 'both win')
        row.teams.forEach((t: Row) => {
          t.won = true;
          t.players.forEach((p: Row) => (p.won = true));
        });
      if (kind === 'player conflicts') row.teams[0].players[0].won = !row.teams[0].won;
      if (kind === 'player outcome missing') delete row.teams[0].players[0].won;
      expect(
        decodeWc3Match(row, 'finished', now).players.every((p) => p.outcome === 'unknown'),
      ).toBe(true);
    },
  );
  it('rejects invalid rosters, times, identifiers and mismatched duration', () => {
    const changes: ((row: Row) => void)[] = [
      (r) => (r.teams[1].players[0].battleTag = r.teams[0].players[0].battleTag),
      (r) => r.teams[1].players.pop(),
      (r) => (r.teams[0].players[0].battleTag = '*'),
      (r) => (r.startTime = '2026-10-30T00:00:00Z'),
      (r) => (r.startTime = '2026-02-30T00:00:00Z'),
      (r) => (r.endTime = '0001-01-01T00:00:00+00:00'),
      (r) => (r.durationInSeconds = 1),
      (r) => (r.id = 123),
      (r) => (r['original-ongoing-match-id'] = 123),
      (r) => (r.floGameId = 9007199254740992),
      (r) => (r.floGameId = r.floMatchId + 1),
      (r) => (r.teams[0].players[0].race = 1.5),
    ];
    for (const change of changes) {
      const row = structuredClone(fixtures.finished.match) as Row;
      change(row);
      expect(() => decodeWc3Match(row, 'finished', now)).toThrow();
    }
    const large = parseProviderJson(
      JSON.stringify(fixtures.finished.match).replace(
        /"flo(?:Game|Match)Id":\d+/g,
        '"floGameId":9007199254740993',
      ),
    ) as Row;
    large.floMatchId = large.floGameId;
    expect(decodeWc3Match(large, 'finished', now).providerIds?.floGameId).toBe('9007199254740993');
  });
});

describe('W3Champions public API contract', () => {
  it('discovers supported ongoing matches while keeping waiting-lobby discovery absent', async () => {
    const { adapter, request } = harness();
    const result = await adapter.discoverOngoing!();
    expect(adapter.discover).toBeUndefined();
    expect(adapter.joinUri(original)).toBe('');
    expect(result).toMatchObject({ status: 'ok', value: [{ nativeMatchId: original, mode: '1' }] });
    if (result.status === 'ok') expect(result.value).toHaveLength(1); // Captured Direct Strike row is not supported.
    expect(request.mock.calls[0][0]).toMatchObject({ cacheTtlMs: 30000, maxBytes: 1500000 });
    expect(adapter.lobbyUrl(original)).toBe('https://w3champions.com/matches');
    expect(adapter.lobbyUrl(ongoing.id)).toBe(`https://w3champions.com/match/${ongoing.id}`);
  });
  it.each([2, 3, 5, 6, 8, 9, 11, 1101, 9999])('rejects mode %i for match binding', async (mode) => {
    const { adapter, bodies } = harness();
    bodies.correlatedFinished.match.gameMode = mode;
    expect(await adapter.match!(original)).toEqual({ status: 'unsupported' });
    expect(await adapter.observe(original, true)).toEqual({ status: 'unsupported' });
  });
  it('correlates the captured ongoing ID to its finished record despite a different database ID', async () => {
    const { adapter, request } = harness();
    const previous = decodeWc3Match(ongoing, 'started', now);
    const result = await adapter.observe(original, true, {
      previous,
      nativeMatchId: original,
      profileIds: previous.players.map((p) => p.profileId),
      matchCreatedAt: previous.startedAt! - 60000,
      attachedAt: previous.startedAt! + 60000,
    });
    expect(result).toMatchObject({
      status: 'ok',
      value: {
        referenceKind: 'match',
        externalId: original,
        nativeMatchId: original,
        phase: 'finished',
        confidence: 'roster',
        providerIds: {
          recordId: fixtures.correlatedFinished.match.id,
          ongoingRecordId: ongoing.id,
          finishedRecordId: fixtures.correlatedFinished.match.id,
        },
      },
    });
    expect(fixtures.correlatedFinished.match.id).not.toBe(ongoing.id);
    expect(request).toHaveBeenCalledTimes(1);
    expect(new URL(request.mock.calls[0][0].url).pathname).toBe(originalPath);
    if (result.status === 'ok')
      expect(result.value.players.filter((p) => p.outcome === 'win')).toHaveLength(1);
  });
  it('pins a supplied ongoing record alias before following its original matchmaking ID', async () => {
    const { adapter, overrides, request } = harness();
    const reference = `record:${ongoing.id}`;
    const first = await adapter.observe(reference, false, {
      profileIds: ongoing.teams.flatMap((t) => t.players.map((p) => p.battleTag)),
    });
    expect(first).toMatchObject({
      status: 'ok',
      value: { externalId: reference, nativeMatchId: original, phase: 'started' },
    });
    if (first.status !== 'ok') throw Error('Missing fixture observation');
    const second = await adapter.observe(reference, true, {
      previous: first.value,
      nativeMatchId: original,
    });
    expect(second).toMatchObject({
      status: 'ok',
      value: { externalId: reference, nativeMatchId: original, phase: 'finished' },
    });
    expect(request.mock.calls.every(([r]) => !r.url.includes('/matches/search'))).toBe(true);
    overrides.set(originalPath, { status: 'not_found' });
    const conflicting = structuredClone(fixtures.correlatedFinished);
    conflicting.match['original-ongoing-match-id'] = 'OtherMatch';
    overrides.set(`/api/matches/${fixtures.correlatedFinished.match.id}`, {
      status: 'ok',
      value: conflicting,
    });
    expect(await adapter.observe(original, true, { nativeMatchId: 'OtherMatch' })).toEqual({
      status: 'invalid_response',
    });
  });
  it('never substitutes a rematch with the same roster or infers a finish from disappearance', async () => {
    const { adapter, overrides, request } = harness();
    const next = structuredClone(ongoing);
    next['original-ongoing-match-id'] = 'Rematch002';
    next.id = 'aaaaaaaaaaaaaaaaaaaaaaaa';
    overrides.set(originalPath, { status: 'not_found' });
    overrides.set(playerPath, { status: 'ok', value: next });
    const context = { previous: decodeWc3Match(ongoing, 'started', now), profileIds: [playerId] };
    expect(await adapter.observe(original, true, context)).toEqual({
      status: 'delayed',
      retryAfterMs: 120000,
    });
    overrides.set(playerPath, { status: 'ok', value: null });
    expect(await adapter.observe(original, true, context)).toEqual({
      status: 'delayed',
      retryAfterMs: 120000,
    });
    const wrong = structuredClone(fixtures.correlatedFinished);
    wrong.match['original-ongoing-match-id'] = 'Rematch002';
    overrides.set(originalPath, { status: 'ok', value: wrong });
    expect(await adapter.observe(original, true, context)).toEqual({ status: 'invalid_response' });
    expect(request.mock.calls.every(([r]) => !r.url.includes('/matches/search'))).toBe(true);
  });
  it('does not let an unproven native-ID hint attach an unrelated record alias', async () => {
    const { adapter, request } = harness();
    const unrelatedRecord = 'bbbbbbbbbbbbbbbbbbbbbbbb';
    expect(
      await adapter.observe(`record:${unrelatedRecord}`, true, { nativeMatchId: original }),
    ).toEqual({ status: 'delayed', retryAfterMs: 120000 });
    expect(new URL(request.mock.calls[0][0].url).pathname).toBe(`/api/matches/${unrelatedRecord}`);
    expect(
      await adapter.observe(`record:${fixtures.correlatedFinished.match.id}`, true, {
        nativeMatchId: 'OtherMatch',
      }),
    ).toEqual({ status: 'invalid_response' });
  });
  it('checks expected roster, creation time and two-team continuity', async () => {
    const { adapter, bodies } = harness();
    expect(await adapter.observe(original, true, { profileIds: ['Unrelated#1234'] })).toEqual({
      status: 'invalid_response',
    });
    expect(
      await adapter.observe(original, true, {
        matchCreatedAt: Date.parse(ongoing.startTime) + 120001,
      }),
    ).toEqual({ status: 'invalid_response' });
    const previous = decodeWc3Match(ongoing, 'started', now);
    // A reversed team array is equivalent; participants changing teams are not.
    bodies.correlatedFinished.match.teams.reverse();
    expect((await adapter.observe(original, true, { previous })).status).toBe('ok');
    previous.players[1].team = previous.players[0].team;
    expect(await adapter.observe(original, true, { previous })).toEqual({
      status: 'invalid_response',
    });
  });
  it('rejects conflicting FLO identity and a regression from finished to ongoing', async () => {
    const { adapter, overrides } = harness();
    const previous = decodeWc3Match(ongoing, 'started', now);
    previous.providerIds!.floGameId = '999';
    expect(await adapter.observe(original, true, { previous })).toEqual({
      status: 'invalid_response',
    });
    overrides.set(originalPath, { status: 'not_found' });
    const finished = decodeWc3Match(fixtures.correlatedFinished.match, 'finished', now);
    expect(await adapter.observe(original, true, { previous: finished })).toEqual({
      status: 'invalid_response',
    });
  });
  it('uses a verified profile and explicit player filter for bounded latest-season history', async () => {
    const { adapter, bodies, request } = harness();
    bodies.profile.participatedInSeasons.reverse();
    bodies.history.matches.reverse();
    expect(await adapter.profile(playerId)).toMatchObject({
      status: 'ok',
      value: { id: playerId, namespace: wc3AccountNamespace },
    });
    const history = await adapter.history!(playerId);
    expect(history.status).toBe('ok');
    if (history.status !== 'ok') throw Error('Missing fixture history');
    expect(history.value[0].nativeMatchId).toBe(original);
    expect(history.value.every((m) => m.players.some((p) => p.profileId === playerId))).toBe(true);
    const url = new URL(request.mock.calls.at(-1)![0].url);
    expect(url.searchParams.get('playerId')).toBe(playerId);
    expect(url.searchParams.get('season')).toBe('25');
    expect(url.searchParams.get('pageSize')).toBe('20');
    expect(url.hash).toBe('');
    bodies.history.matches[0].teams.forEach((t) =>
      t.players.forEach((p) => {
        if (p.battleTag === playerId) p.battleTag = 'OtherPlayer#1234';
      }),
    );
    expect(await adapter.history!(playerId)).toEqual({ status: 'invalid_response' });
  });
  it('distinguishes empty history, missing profile, malformed profile and renamed account', async () => {
    const { adapter, bodies, overrides, request } = harness();
    bodies.profile.participatedInSeasons = [];
    expect(await adapter.history!(playerId)).toEqual({ status: 'ok', value: [] });
    expect(request).toHaveBeenCalledTimes(1);
    const path = `/api/players/${encodeURIComponent(playerId)}`;
    overrides.set(path, { status: 'not_found' });
    expect(await adapter.history!(playerId)).toEqual({ status: 'not_found' });
    overrides.set(path, { status: 'ok', value: {} });
    expect(await adapter.profile(playerId)).toEqual({ status: 'invalid_response' });
    overrides.set(path, {
      status: 'ok',
      value: { ...fixtures.profile, battleTag: 'Renamed#1234' },
    });
    expect(await adapter.profile(playerId)).toEqual({ status: 'invalid_response' });
  });
  it.each(['throttled', 'authentication_required', 'unavailable', 'invalid_response'] as const)(
    'passes %s through without an empty successful feed',
    async (status) => {
      const failure = { status, retryAfterMs: 42000 };
      const adapter = createWc3Adapter({ request: async () => failure });
      expect(await adapter.discoverOngoing!()).toEqual(failure);
      expect(await adapter.observe(original, true)).toEqual(failure);
      expect(await adapter.profile(playerId)).toEqual(failure);
      expect(await adapter.history!(playerId)).toEqual(failure);
      expect(await adapter.replays!(original)).toEqual(failure);
    },
  );
  it.each([
    [429, 'throttled'],
    [401, 'authentication_required'],
    [403, 'authentication_required'],
    [500, 'invalid_response'],
  ] as const)('normalizes HTTP-200 application error %i', async (status, expected) => {
    const adapter = createWc3Adapter({
      request: async () => ({ status: 'ok', value: { status } }),
    });
    expect(await adapter.discoverOngoing!()).toEqual({ status: expected });
  });
  it.each([
    {},
    null,
    [],
    { matches: [] },
    { matches: [], count: '0' },
    { matches: [{}], count: 1 },
  ])('rejects malformed discovery %j', async (value) => {
    const adapter = createWc3Adapter({ request: async () => ({ status: 'ok', value }) });
    expect(await adapter.discoverOngoing!()).toEqual({ status: 'invalid_response' });
  });
  it('caps discovery at two pages and does not hide a second-page failure', async () => {
    const request = vi.fn<JsonTransport>().mockImplementation(async ({ url }) => {
      const offset = Number(new URL(url).searchParams.get('offset'));
      const matches = Array.from({ length: 50 }, (_, index) => ({
        ...ongoing,
        id: (offset + index + 1).toString(16).padStart(24, '0'),
        'original-ongoing-match-id': `FixtureMatch${offset + index}`,
      }));
      return { status: 'ok', value: { matches, count: 1000 } };
    });
    const adapter = createWc3Adapter({ request });
    const result = await adapter.discoverOngoing!();
    expect(result.status).toBe('ok');
    if (result.status === 'ok') expect(result.value).toHaveLength(100);
    expect(request).toHaveBeenCalledTimes(2);
    request.mockClear();
    request.mockImplementationOnce(async () => ({
      status: 'ok',
      value: { matches: Array(50).fill(ongoing), count: 100 },
    }));
    request.mockImplementationOnce(async () => ({ status: 'throttled', retryAfterMs: 5000 }));
    expect(await adapter.discoverOngoing!()).toEqual({ status: 'throttled', retryAfterMs: 5000 });
  });
  it('returns only an unknown-availability provider page for replay references', async () => {
    const { adapter, request } = harness();
    expect(await adapter.replays!(original)).toEqual({
      status: 'ok',
      value: [
        {
          provider: 'w3champions',
          matchId: original,
          availability: 'unknown',
          sourceUrl: `https://w3champions.com/match/${fixtures.correlatedFinished.match.id}`,
        },
      ],
    });
    expect(request).toHaveBeenCalledTimes(1);
    expect(request.mock.calls.every(([r]) => !new URL(r.url).pathname.includes('/replays'))).toBe(
      true,
    );
  });
});

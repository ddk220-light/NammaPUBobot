import { afterEach, describe, expect, it, vi } from 'vitest';
import { createBarAdapter, parseBarBattle, parseBarReplay } from '../src/games/bar';
import {
  createGeneralsOnlineAdapter,
  parseGeneralsLobby,
  parseGeneralsMatch,
} from '../src/games/generals-online';
import { parseProviderJson } from '../src/games/transport';
import type { JsonRequest, JsonTransport, ProviderResult } from '../src/games/types';
import barFixture from './fixtures/connectors/bar-v1.json';
import generalsFixture from './fixtures/connectors/generals-online-v1.json';

const now = Date.parse('2026-10-28T23:00:00Z');
const hash = barFixture.replay.id;
const nativeMatch = generalsFixture.match.match_id;
const nativeLobby = generalsFixture.lobby.LobbyID;
const ok = (value: unknown): ProviderResult<unknown> => ({ status: 'ok', value });
const clone = <T>(v: T): T => structuredClone(v);
afterEach(() => vi.restoreAllMocks());

function transport(routes: Record<string, ProviderResult<unknown>>) {
  const calls: JsonRequest[] = [];
  const request: JsonTransport = async (req) => {
    calls.push(req);
    return clone(
      routes[new URL(req.url).pathname + new URL(req.url).search] ??
        routes[new URL(req.url).pathname] ?? { status: 'not_found' },
    );
  };
  return { request, calls };
}

describe('BAR metadata connector', () => {
  it('separates battle ID, server match ID and replay hash, and wall-clock from simulation duration', () => {
    const battle = parseBarBattle(barFixture.battle, now),
      match = parseBarReplay(barFixture.replay, hash, now);
    expect(battle.nativeLobbyId).toBe('71');
    expect(battle.nativeMatchId).toBeUndefined();
    expect(match.nativeMatchId).toBe('11287420');
    expect(match.nativeLobbyId).toBeUndefined();
    expect(match.replayReferences?.[0].matchId).toBe(hash);
    expect(match.simulationDuration).toEqual({ value: 403.9, unit: 'seconds' });
    expect(match.elapsedDuration).toEqual({ value: 470, unit: 'seconds' });
    expect(match.finishedAt).toBe(Date.parse(barFixture.replay.startTime) + 470000);
    expect(match.players.map((p) => p.outcome)).toEqual(['win', 'loss']);
    expect(match.players.map((p) => p.team)).toEqual(['ally:0', 'ally:1']);
  });
  it('does not confuse engine teams with ally teams or fabricate a live start timestamp', () => {
    const battle = parseBarBattle(
      { ...barFixture.battle, lobbyStatus: 'running', gameTime: 55 },
      now,
    );
    expect(battle).toMatchObject({ schemaVersion: 2, phase: 'started', startedAt: null });
    expect(battle.players.every((p) => p.team === null)).toBe(true);
    expect(battle.simulationDuration).toBeUndefined();
    const withSpectator = {
      ...barFixture.battle,
      players: [
        ...barFixture.battle.players,
        { userId: 9, username: 'Spectator', gameStatus: 'Spectating' },
        { userId: 10, username: 'Stale game client', gameStatus: 'Playing' },
      ],
    };
    expect(parseBarBattle(withSpectator, now).players).toHaveLength(2);
  });
  it('leaves outcomes unknown for abnormal endings, bots and missing winner', () => {
    const abnormal = { ...barFixture.replay, gameEndedNormally: false };
    expect(parseBarReplay(abnormal, hash, now).players.every((p) => p.outcome === 'unknown')).toBe(
      true,
    );
    expect(
      parseBarReplay({ ...barFixture.replay, hasBots: true }, hash, now).players.every(
        (p) => p.outcome === 'unknown',
      ),
    ).toBe(true);
    const noWinner = clone(barFixture.replay);
    noWinner.AllyTeams.forEach((t) => (t.winningTeam = false));
    expect(parseBarReplay(noWinner, hash, now).players.every((p) => p.outcome === 'unknown')).toBe(
      true,
    );
  });
  it('preserves unknown factions and missing durations as unknown', () => {
    const r = clone(barFixture.replay) as Record<string, any>;
    r.AllyTeams[0].Players[0].faction = 'FutureFaction';
    delete r.durationMs;
    delete r.fullDurationMs;
    const m = parseBarReplay(r, hash, now);
    expect(m.players[0].selection).toBe('FutureFaction');
    expect(m.finishedAt).toBeNull();
    expect(m.elapsedDuration).toBeUndefined();
    expect(m.simulationDuration).toBeUndefined();
  });
  it('preserves oversized account IDs and rejects already-rounded numeric IDs', () => {
    const raw = JSON.stringify(barFixture.replay).replace('1001', '9007199254740993');
    expect(parseBarReplay(parseProviderJson(raw), hash, now).players[0].profileId).toBe(
      '9007199254740993',
    );
    expect(() => parseBarReplay(JSON.parse(raw), hash, now)).toThrow();
  });
  it('returns delayed for disappeared/reused battles rather than correlating a same-roster replay', async () => {
    vi.spyOn(Date, 'now').mockReturnValue(now);
    const t = transport({ '/battles': ok([barFixture.battle]) });
    const a = createBarAdapter(t);
    const previous = parseBarBattle({ ...barFixture.battle, lobbyStatus: 'running' }, now - 5000);
    expect((await a.observe('71', true, { previous, profileIds: ['1001', '1009'] })).status).toBe(
      'delayed',
    );
    expect((await a.observe('72', true)).status).toBe('delayed');
    expect(t.calls.every((c) => new URL(c.url).pathname === '/battles')).toBe(true);
  });
  it('accepts an explicit replay-page association without reinterpreting it as a battle ID', async () => {
    vi.spyOn(Date, 'now').mockReturnValue(now);
    const t = transport({ [`/replays/${hash}`]: ok(barFixture.replay) });
    const a = createBarAdapter(t);
    const ref = a.parseLobbyReference(`https://bar-rts.com/replays/${hash}`);
    expect(ref).toBe(hash);
    const observed = await a.observe(ref, false);
    expect(observed.status).toBe('ok');
    if (observed.status === 'ok') expect(observed.value.nativeLobbyId).toBeUndefined();
    await a.replays!(hash);
    expect(t.calls.every((c) => c.url === `https://api.bar-rts.com/replays/${hash}`)).toBe(true);
  });
  it('resolves bounded live accounts and does not claim an offline account is nonexistent', async () => {
    const t = transport({ '/battles': ok([barFixture.battle]) });
    const a = createBarAdapter(t);
    expect(await a.profile('1001')).toMatchObject({
      status: 'ok',
      value: { id: '1001', namespace: 'bar:user' },
    });
    expect((await a.profile('99')).status).toBe('delayed');
    expect(t.calls.some((c) => c.url.includes('cached-users'))).toBe(false);
  });
  it('uses names only to find candidates, verifies IDs and sorts unsorted history', async () => {
    vi.spyOn(Date, 'now').mockReturnValue(now);
    const olderHash = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
    const strangerHash = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';
    const older = { ...barFixture.replay, id: olderHash, startTime: '2026-10-28T17:00:00Z' };
    const stranger = clone(barFixture.replay);
    stranger.id = strangerHash;
    stranger.AllyTeams[0].Players[0].userId = 999;
    const t = transport({
      '/battles': ok([barFixture.battle]),
      '/replays': ok({ data: [{ id: olderHash }, { id: strangerHash }, { id: hash }] }),
      [`/replays/${hash}`]: ok(barFixture.replay),
      [`/replays/${olderHash}`]: ok(older),
      [`/replays/${strangerHash}`]: ok(stranger),
    });
    const r = await createBarAdapter(t).history!('1001');
    expect(r.status).toBe('ok');
    if (r.status === 'ok')
      expect(r.value.map((m) => m.replayReferences?.[0].matchId)).toEqual([hash, olderHash]);
    expect(t.calls.find((c) => new URL(c.url).pathname === '/replays')?.url).toContain('limit=5');
  });
  it('distinguishes schema errors, upstream failures and valid empty lists', async () => {
    for (const value of [{ error: 'failure' }, { data: [] }, null])
      expect(
        (await createBarAdapter(transport({ '/battles': ok(value) })).discover!()).status,
      ).toBe('invalid_response');
    expect(await createBarAdapter(transport({ '/battles': ok([]) })).discover!()).toEqual({
      status: 'ok',
      value: [],
    });
    expect(
      await createBarAdapter(transport({ '/battles': { status: 'throttled', retryAfterMs: 9000 } }))
        .discover!(),
    ).toEqual({ status: 'throttled', retryAfterMs: 9000 });
    const mismatch = { ...barFixture.replay, id: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' };
    expect(
      (await createBarAdapter(transport({ [`/replays/${hash}`]: ok(mismatch) })).match!(hash))
        .status,
    ).toBe('invalid_response');
  });
  it('rejects untrusted URLs and does not promise year-old binary availability', () => {
    const a = createBarAdapter();
    for (const url of [
      `https://evil.example/replays/${hash}`,
      `https://user@bar-rts.com/replays/${hash}`,
      `file:///replays/${hash}`,
    ])
      expect(() => a.parseLobbyReference(url)).toThrow();
    const old = { ...barFixture.replay, startTime: '2024-01-01T00:00:00Z' };
    expect(parseBarReplay(old, hash, now).replayReferences?.[0].availability).toBe('unknown');
  });
});

describe('credentialed GeneralsOnline Zero Hour connector', () => {
  const route = `/env/prod/contract/1/MatchHistory/${nativeMatch}`;
  const result = () => ok({ matches: { matches: [generalsFixture.match] } });
  it('does not issue protected requests without an integration key', async () => {
    const request = vi.fn<JsonTransport>();
    const a = createGeneralsOnlineAdapter({ request });
    expect(await a.discover!()).toEqual({ status: 'authentication_required' });
    expect(await a.match!(nativeMatch)).toEqual({ status: 'authentication_required' });
    expect(await a.replays!(nativeMatch)).toEqual({ status: 'authentication_required' });
    expect(await a.profile('101')).toEqual({ status: 'unsupported' });
    expect(a.history).toBeUndefined();
    expect(request).not.toHaveBeenCalled();
  });
  it('keeps native IDs lossless, ignores observers and retains unverified duration units', () => {
    const l = parseGeneralsLobby(generalsFixture.lobby, now);
    expect(l.nativeLobbyId).toBe(nativeLobby);
    expect(l.nativeMatchId).toBe(nativeMatch);
    expect(l.players.map((p) => p.profileId)).toEqual(['101', '102']);
    expect(l).toMatchObject({ phase: 'started', startedAt: null });
    const m = parseGeneralsMatch(generalsFixture.match, nativeMatch, now);
    expect(m.nativeLobbyId).toBeUndefined();
    expect(m.players.map((p) => p.outcome)).toEqual(['win', 'loss']);
    expect(m.elapsedDuration).toEqual({ value: 1500, unit: 'seconds' });
    expect(m.simulationDuration).toBeUndefined();
  });
  it('selects only the exact match from the documented 100-ID range', async () => {
    vi.spyOn(Date, 'now').mockReturnValue(now);
    const another = { ...generalsFixture.match, match_id: '9007199254741102' };
    const t = transport({
      [route]: ok({ matches: { matches: [another, generalsFixture.match] } }),
    });
    const a = createGeneralsOnlineAdapter({ ...t, generalsOnlineApiKey: 'fixture-key' });
    const r = await a.match!(nativeMatch);
    expect(r.status).toBe('ok');
    if (r.status === 'ok') expect(r.value.nativeMatchId).toBe(nativeMatch);
    expect(t.calls[0].headers).toEqual({ 'X-Api-Key': 'fixture-key' });
    expect(t.calls[0].url).toBe(`https://api.playgenerals.online${route}`);
  });
  it('pins lobby-to-match continuity when the same room is reused', async () => {
    vi.spyOn(Date, 'now').mockReturnValue(now);
    const t = transport({
      [route]: result(),
      '/env/prod/contract/1/Lobbies': ok({
        lobbies: [{ ...generalsFixture.lobby, MatchID: '9007199254741200' }],
      }),
    });
    const a = createGeneralsOnlineAdapter({ ...t, generalsOnlineApiKey: 'fixture-key' });
    const r = await a.observe(nativeLobby, true, { nativeMatchId: nativeMatch });
    expect(r.status).toBe('ok');
    if (r.status === 'ok')
      expect(r.value).toMatchObject({
        externalId: nativeLobby,
        nativeLobbyId: nativeLobby,
        nativeMatchId: nativeMatch,
      });
    expect(t.calls).toHaveLength(1);
  });
  it('does not turn desync, missing outcomes, AI games or all-lost into results', () => {
    for (const mutate of [
      (m: any) => (m.members[0].desynced = true),
      (m: any) => delete m.members[1].won,
      (m: any) => (m.members[0].won = false),
      (m: any) => m.members.push({ slot_state: 2 }),
    ]) {
      const m = clone(generalsFixture.match);
      mutate(m);
      expect(
        parseGeneralsMatch(m, nativeMatch, now).players.every((p) => p.outcome === 'unknown'),
      ).toBe(true);
    }
  });
  it('preserves unknown faction IDs and returns provider references only', () => {
    const m = clone(generalsFixture.match);
    m.members[0].side = 999;
    const r = parseGeneralsMatch(m, nativeMatch, now);
    expect(r.players[0]).toMatchObject({ selectionId: '999', selection: 'Faction 999' });
    expect(r.replayReferences?.[0]).toMatchObject({
      availability: 'available',
      matchId: nativeMatch,
      perspectiveId: '101',
      sourceUrl: `https://strata.gamereplays.org/zh/match/${nativeMatch}`,
    });
    expect(r.replayReferences?.[1].availability).toBe('unknown');
  });
  it('reports missing publication as delayed and HTTP-200 errors as invalid', async () => {
    const mk = (value: unknown) =>
      createGeneralsOnlineAdapter({
        ...transport({ [route]: ok(value) }),
        generalsOnlineApiKey: 'fixture-key',
      });
    expect(await mk({ matches: { matches: [] } }).match!(nativeMatch)).toEqual({
      status: 'delayed',
      retryAfterMs: 120000,
    });
    for (const v of [{ errors: { key: 'denied' } }, { matches: null }, { matches: [] }])
      expect((await mk(v).match!(nativeMatch)).status).toBe('invalid_response');
    const t = transport({ [route]: { status: 'authentication_required' } });
    expect(
      (
        await createGeneralsOnlineAdapter({ ...t, generalsOnlineApiKey: 'revoked' }).match!(
          nativeMatch,
        )
      ).status,
    ).toBe('authentication_required');
  });
  it('rejects duplicate identities, wrong states and timezone-ambiguous timestamps', () => {
    const m = clone(generalsFixture.match);
    m.members[1].user_id = '101';
    expect(() => parseGeneralsMatch(m, nativeMatch, now)).toThrow();
    expect(() =>
      parseGeneralsMatch(
        { ...generalsFixture.match, time_started: '2026-10-28T18:00:00' },
        nativeMatch,
        now,
      ),
    ).toThrow();
    expect(() => parseGeneralsLobby({ ...generalsFixture.lobby, State: '1' }, now)).toThrow();
  });
  it('distinguishes a lobby ID from an explicitly supplied match link', () => {
    const a = createGeneralsOnlineAdapter();
    expect(a.parseLobbyReference(nativeLobby)).toBe(nativeLobby);
    expect(a.parseLobbyReference(`https://strata.gamereplays.org/zh/match/${nativeMatch}`)).toBe(
      `match:${nativeMatch}`,
    );
    expect(() =>
      a.parseLobbyReference(`https://strata.gamereplays.org.evil.example/zh/match/${nativeMatch}`),
    ).toThrow();
  });
});

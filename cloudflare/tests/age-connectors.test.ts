import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createAoE3Adapter, aoe3Configuration } from '../src/games/aoe3';
import { createAoE4Adapter, enrichAoE4Match } from '../src/games/aoe4';
import { createAoMAdapter } from '../src/games/aom';
import { ageIdentifier, decodeAgeMatch, worldEdgeEnvelope } from '../src/games/worlds-edge';
import { parseProviderJson } from '../src/games/transport';
import type { AdapterRuntime, GameAdapter, JsonRequest, ProviderResult } from '../src/games/types';
import aoe3Fixture from './fixtures/connectors/age-aoe3-v1.json';
import aoe4Fixture from './fixtures/connectors/age-aoe4-v1.json';
import aomFixture from './fixtures/connectors/age-aom-v1.json';

type Data = Record<string, any>;
const now = Date.parse('2026-10-28T20:00:00Z');
const samples = [
  ['aoe3', createAoE3Adapter, aoe3Fixture, 'age3', 'aoe-api.worldsedgelink.com'],
  ['aoe4', createAoE4Adapter, aoe4Fixture, 'age4', 'aoe-api.worldsedgelink.com'],
  ['aom', createAoMAdapter, aomFixture, 'athens', 'athens-live-api.worldsedgelink.com'],
] as const;
function harness(factory: (runtime?: AdapterRuntime) => GameAdapter, fixture: Data) {
  const bodies: Record<string, unknown> = {
    getPersonalStat: fixture.profile,
    findAdvertisements: fixture.lobbies,
    getMatchHistory: fixture.match,
    getRecentMatchHistory: fixture.history,
    getReplayFiles: fixture.replays,
  };
  const request = vi.fn(async ({ url }: JsonRequest): Promise<ProviderResult<unknown>> => ({
    status: 'ok',
    value: structuredClone(bodies[new URL(url).pathname.split('/').at(-1)!]),
  }));
  return { adapter: factory({ request }), request, bodies };
}
beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(now);
});
afterEach(() => vi.useRealTimers());

/** One reusable operation contract, run against each title's captured schema. */
describe.each(samples)('%s World’s Edge contract', (_id, factory, fixture, title, host) => {
  it('resolves stable profiles without treating existence as ownership proof', async () => {
    const { adapter, request } = harness(factory, fixture);
    const profileId = String(fixture.profile.statGroups[0].members[0].profile_id);
    const result = await adapter.profile(profileId);
    expect(result).toMatchObject({
      status: 'ok',
      value: { id: profileId, namespace: `worlds-edge:${title}` },
    });
    expect(new URL(request.mock.calls[0][0].url).hostname).toBe(host);
    expect(new URL(request.mock.calls[0][0].url).searchParams.get('title')).toBe(title);
    expect(adapter.manifest.acceptance).toBe('pending');
    expect(result.status === 'ok' && 'verified' in result.value).toBe(false);
  });
  it('resolves only the exact Steam identity', async () => {
    const { adapter } = harness(factory, fixture);
    expect(await adapter.resolveSteam!('76561198000000001')).toMatchObject({ status: 'ok' });
    expect(await adapter.resolveSteam!('76561198000000999')).toEqual({ status: 'not_found' });
  });
  it('decodes advertisements without inventing start evidence or rounding platform IDs', async () => {
    const { adapter } = harness(factory, fixture);
    const result = await adapter.discover!();
    expect(result.status).toBe('ok');
    if (result.status !== 'ok') return;
    expect(result.value.length).toBe(2);
    expect(
      result.value.every((lobby) => lobby.startedAt === null && lobby.finishedAt === null),
    ).toBe(true);
    const platform = fixture.lobbies.matches.find((m) => String(m.steamlobbyid) !== '0');
    if (platform)
      expect(
        result.value.find((m) => m.nativeLobbyId === String(platform.id))?.platformLobbyId,
      ).toBe(String(platform.steamlobbyid));
    expect(result.value[0].nativeMatchId).toBeUndefined();
  });
  it('keeps participants, outcomes, selection IDs and elapsed seconds', async () => {
    const { adapter } = harness(factory, fixture);
    const raw = fixture.match.matchHistory[0];
    const result = await adapter.match!(String(raw.id));
    expect(result.status).toBe('ok');
    if (result.status !== 'ok') return;
    expect(result.value.nativeMatchId).toBe(String(raw.id));
    expect(result.value.nativeLobbyId).toBeUndefined();
    expect(result.value.elapsedDuration).toEqual({
      value: raw.completiontime - raw.startgametime,
      unit: 'seconds',
    });
    expect(result.value.players).toHaveLength(raw.matchhistoryreportresults.length);
    expect(result.value.players[0]).toMatchObject({
      profileId: String(raw.matchhistoryreportresults[0].profile_id),
      team: `we:${raw.matchhistoryreportresults[0].teamid}`,
      selectionId: String(raw.matchhistoryreportresults[0].civilization_id),
      outcome: raw.matchhistoryreportresults[0].resulttype === 1 ? 'win' : 'loss',
    });
  });
  it('sorts history by time and preserves unknown selections and outcomes', async () => {
    const { adapter, bodies } = harness(factory, fixture);
    const history = structuredClone(fixture.history) as Data;
    history.matchHistoryStats.reverse();
    history.matchHistoryStats[0].matchhistoryreportresults[0].civilization_id = 999999;
    delete history.matchHistoryStats[0].matchhistoryreportresults[0].resulttype;
    bodies.getRecentMatchHistory = history;
    const result = await adapter.history!('123');
    expect(result.status).toBe('ok');
    if (result.status !== 'ok') return;
    expect(result.value[0].startedAt! >= result.value[1].startedAt!).toBe(true);
    const unknown = result.value.flatMap((m) => m.players).find((p) => p.selectionId === '999999');
    expect(unknown?.selection).toContain('999999 (unknown)');
    expect(unknown?.outcome).toBe('unknown');
  });
  it('distinguishes HTTP200 application errors, wrong identities and private histories', async () => {
    const { adapter, bodies } = harness(factory, fixture);
    bodies.getPersonalStat = { result: { code: 2, message: 'NOT_FOUND' } };
    expect(await adapter.profile('123')).toEqual({ status: 'not_found' });
    expect(await adapter.match!('999')).toEqual({ status: 'invalid_response' });
    bodies.getRecentMatchHistory = { result: { code: 0, message: 'SUCCESS' } };
    expect(await adapter.history!('123')).toEqual({ status: 'invalid_response' });
    bodies.getRecentMatchHistory = { result: { code: 9, message: 'PRIVATE_HISTORY' } };
    expect(await adapter.history!('123')).toEqual({ status: 'authentication_required' });
  });
  it('keeps missing or ambiguous lobby-to-result continuity delayed', async () => {
    const { adapter, bodies } = harness(factory, fixture);
    bodies.findAdvertisements = { result: { code: 0 }, matches: [], avatars: [] };
    bodies.getMatchHistory = { result: { code: 2, message: 'NOT_FOUND' } };
    const m = fixture.history.matchHistoryStats[0];
    const context = {
      attachedAt: m.startgametime * 1000,
      profileIds: m.matchhistoryreportresults.map((p) => String(p.profile_id)),
    };
    expect(await adapter.observe('998', false)).toEqual({
      status: 'delayed',
      retryAfterMs: 120000,
    });
    const duplicate = structuredClone(m);
    duplicate.id = 999;
    duplicate.startgametime += 20;
    duplicate.completiontime += 20;
    duplicate.matchhistoryreportresults.forEach((p) => {
      p.matchhistory_id = 999;
    });
    duplicate.matchhistorymember.forEach((p) => {
      p.matchhistory_id = 999;
    });
    duplicate.matchurls = [];
    bodies.getRecentMatchHistory = {
      result: { code: 0 },
      matchHistoryStats: [m, duplicate],
      profiles: fixture.history.profiles,
    };
    expect(await adapter.observe('998', true, context)).toEqual({
      status: 'delayed',
      retryAfterMs: 120000,
    });
  });
  it('binds a unique result only with exact participants and attachment-time evidence', async () => {
    const { adapter, bodies } = harness(factory, fixture);
    bodies.findAdvertisements = { result: { code: 0 }, matches: [], avatars: [] };
    bodies.getMatchHistory = { result: { code: 2, message: 'NOT_FOUND' } };
    const m = fixture.history.matchHistoryStats[0];
    bodies.getRecentMatchHistory = {
      result: { code: 0 },
      matchHistoryStats: [m],
      profiles: fixture.history.profiles,
    };
    const context = {
      attachedAt: m.startgametime * 1000,
      profileIds: m.matchhistoryreportresults.map((p) => String(p.profile_id)),
    };
    expect(await adapter.observe('998', false, context)).toMatchObject({
      status: 'ok',
      value: {
        externalId: '998',
        nativeLobbyId: '998',
        nativeMatchId: String(m.id),
        confidence: 'roster',
      },
    });
    expect(
      await adapter.observe('998', false, {
        ...context,
        attachedAt: m.completiontime * 1000 + 120001,
      }),
    ).toMatchObject({ status: 'delayed' });
    expect(
      await adapter.observe('998', false, { ...context, profileIds: ['1', '2'] }),
    ).toMatchObject({ status: 'delayed' });
    bodies.getMatchHistory = {
      result: { code: 0 },
      matchHistory: [m],
      profiles: fixture.history.profiles,
    };
    const result = await adapter.match!(String(m.id));
    if (result.status !== 'ok') throw Error('fixture');
    expect(
      await adapter.observe(String(m.id), false, {
        ...context,
        previous: {
          ...result.value,
          externalId: '998',
          phase: 'open',
          startedAt: null,
          finishedAt: null,
          observedAt: m.completiontime * 1000,
        },
      }),
    ).toMatchObject({ status: 'delayed' });
  });
  it('accepts late explicit native-ID attachment but keeps the narrower history window', async () => {
    const { adapter, bodies, request } = harness(factory, fixture);
    bodies.findAdvertisements = { result: { code: 0 }, matches: [], avatars: [] };
    const m = fixture.match.matchHistory[0];
    const context = {
      matchCreatedAt: m.startgametime * 1000 - 300000,
      attachedAt: m.startgametime * 1000 + 600000,
      profileIds: m.matchhistoryreportresults.map((p) => String(p.profile_id)),
    };
    expect(await adapter.observe(String(m.id), false, context)).toMatchObject({
      status: 'ok',
      value: { nativeMatchId: String(m.id), nativeLobbyId: String(m.id) },
    });
    expect(
      request.mock.calls.some(([r]) => new URL(r.url).pathname.endsWith('/getRecentMatchHistory')),
    ).toBe(false);
    bodies.getMatchHistory = { result: { code: 2, message: 'NOT_FOUND' } };
    bodies.getRecentMatchHistory = {
      result: { code: 0 },
      matchHistoryStats: [m],
      profiles: fixture.match.profiles,
    };
    expect(await adapter.observe('998', false, context)).toMatchObject({ status: 'delayed' });
  });
  it('rejects an exact native ID whose game started before the bot match existed', async () => {
    const { adapter, bodies } = harness(factory, fixture);
    bodies.findAdvertisements = { result: { code: 0 }, matches: [], avatars: [] };
    bodies.getRecentMatchHistory = { result: { code: 0 }, matchHistoryStats: [], profiles: [] };
    const m = fixture.match.matchHistory[0];
    const context = {
      matchCreatedAt: m.startgametime * 1000 + 120001,
      attachedAt: m.startgametime * 1000 + 600000,
      profileIds: m.matchhistoryreportresults.map((p) => String(p.profile_id)),
    };
    expect(await adapter.observe(String(m.id), false, context)).toMatchObject({
      status: 'delayed',
    });
    expect(
      await adapter.observe(String(m.id), false, { ...context, nativeMatchId: String(m.id) }),
    ).toMatchObject({ status: 'invalid_response' });
  });
  it('rejects duplicate participants and future times', async () => {
    const { adapter, bodies } = harness(factory, fixture);
    const data = structuredClone(fixture.match);
    data.matchHistory[0].matchhistoryreportresults.push(
      data.matchHistory[0].matchhistoryreportresults[0],
    );
    bodies.getMatchHistory = data;
    expect(await adapter.match!(String(data.matchHistory[0].id))).toEqual({
      status: 'invalid_response',
    });
    data.matchHistory[0].matchhistoryreportresults.pop();
    data.matchHistory[0].completiontime = now / 1000 + 1;
    expect(await adapter.match!(String(data.matchHistory[0].id))).toEqual({
      status: 'invalid_response',
    });
  });
  it('passes throttling through instead of producing an empty success', async () => {
    const adapter = factory({
      request: async () => ({ status: 'throttled', retryAfterMs: 42000 }),
    });
    expect(await adapter.discover!()).toEqual({ status: 'throttled', retryAfterMs: 42000 });
    expect(await adapter.history!('123')).toEqual({ status: 'throttled', retryAfterMs: 42000 });
  });
  it('requests replay metadata without caching or downloading bytes', async () => {
    const { adapter, request } = harness(factory, fixture);
    const replay = fixture.replays as Data;
    const matchId = replay.replayFiles?.length
      ? String(replay.replayFiles[0].matchhistory_id)
      : String(fixture.match.matchHistory[0].id);
    expect((await adapter.replays!(matchId)).status).toBe('ok');
    expect(request.mock.calls).toHaveLength(1);
    expect(new URL(request.mock.calls[0][0].url).pathname).toBe(
      '/community/leaderboard/getReplayFiles',
    );
    expect(request.mock.calls[0][0].cacheTtlMs).toBe(0);
  });
});
describe('Age evidence semantics', () => {
  it('preserves lossless identifiers and rejects rounded numbers', () => {
    expect(ageIdentifier('109775242664684177')).toBe('109775242664684177');
    expect(
      ageIdentifier((parseProviderJson('{"id":109775242664684177}') as { id: string }).id),
    ).toBe('109775242664684177');
    expect(() => ageIdentifier(109775242664684177)).toThrow('Unsafe identifier');
    expect(() => ageIdentifier('1e20')).toThrow();
  });
  it('distinguishes malformed envelopes and application errors', () => {
    expect(worldEdgeEnvelope({})).toEqual({ status: 'invalid_response' });
    expect(worldEdgeEnvelope({ result: { code: 88, message: 'THROTTLED' } })).toEqual({
      status: 'throttled',
      retryAfterMs: 120000,
    });
    expect(worldEdgeEnvelope({ result: { code: 88, message: 'MAINTENANCE' } })).toEqual({
      status: 'unavailable',
    });
  });
  it('separates AoE4 simulation and elapsed duration', async () => {
    const { adapter } = harness(createAoE4Adapter, aoe4Fixture);
    expect(await adapter.match!('257612387')).toMatchObject({
      status: 'ok',
      value: {
        elapsedDuration: { value: 1630, unit: 'seconds' },
        simulationDuration: { value: 1497, unit: 'seconds' },
      },
    });
  });
  it('keeps custom FFA teams without converting them into two teams', async () => {
    const { adapter, bodies } = harness(createAoE4Adapter, aoe4Fixture);
    bodies.getMatchHistory = aoe4Fixture.custom;
    const result = await adapter.match!(String(aoe4Fixture.custom.matchHistory[0].id));
    expect(result.status).toBe('ok');
    if (result.status !== 'ok') return;
    expect(result.value.mode).toBe('Custom');
    expect(new Set(result.value.players.map((p) => p.team)).size).toBeGreaterThan(2);
  });
  it('retains publisher evidence when optional World enrichment returns404', async () => {
    const { adapter } = harness(createAoE4Adapter, aoe4Fixture);
    const result = await adapter.match!('257612387');
    if (result.status !== 'ok') throw Error('fixture');
    expect(
      await enrichAoE4Match(result.value, '4635035', {
        request: async () => ({ status: 'not_found' }),
      }),
    ).toEqual(result);
  });
  it('does not guess game-time from AoE3 counters', () => {
    const raw = structuredClone(aoe3Fixture.match.matchHistory[0]);
    raw.matchhistoryreportresults[0].counters = '{"gt":100}';
    expect(decodeAgeMatch(raw, new Map(), aoe3Configuration).simulationDuration).toBeUndefined();
  });
  it('accepts captured AoM histories whose two collections renumber equivalent teams', async () => {
    const { adapter, bodies } = harness(createAoMAdapter, aomFixture);
    bodies.getMatchHistory = aomFixture.renumberedTeams;
    const result = await adapter.match!('36847467');
    expect(result.status).toBe('ok');
    if (result.status !== 'ok') return;
    expect(result.value.players.every((p) => p.team !== null)).toBe(true);
    const mismatched = structuredClone(aomFixture.renumberedTeams);
    mismatched.matchHistory[0].matchhistorymember.forEach((p) => {
      p.teamid = 0;
    });
    bodies.getMatchHistory = mismatched;
    const conflict = await adapter.match!('36847467');
    expect(conflict.status).toBe('ok');
    if (conflict.status === 'ok')
      expect(conflict.value.players.every((p) => p.team === null && p.outcome === 'unknown')).toBe(
        true,
      );
  });
  it('retains conflicting report/member outcomes as unknown', () => {
    const raw = structuredClone(aoe3Fixture.match.matchHistory[0]);
    const report = raw.matchhistoryreportresults[0];
    const member = raw.matchhistorymember.find((p) => p.profile_id === report.profile_id)!;
    member.outcome = report.resulttype === 1 ? 0 : 1;
    const match = decodeAgeMatch(raw, new Map(), aoe3Configuration);
    expect(match.players.find((p) => p.profileId === String(report.profile_id))?.outcome).toBe(
      'unknown',
    );
  });
  it('shows unavailable recordings honestly and removes expired signed URLs', async () => {
    const age3 = harness(createAoE3Adapter, aoe3Fixture);
    expect(await age3.adapter.replays!('66376255')).toEqual({
      status: 'ok',
      value: [{ provider: 'worlds-edge:age3', matchId: '66376255', availability: 'unavailable' }],
    });
    const aom = harness(createAoMAdapter, aomFixture);
    const expired = await aom.adapter.replays!('44987874');
    expect(expired.status).toBe('ok');
    if (expired.status !== 'ok') return;
    expect(
      expired.value.every((r) => r.availability === 'expired' && r.sourceUrl === undefined),
    ).toBe(true);
    aom.bodies.getReplayFiles = { ...aomFixture.replays, expiryUnix: now / 1000 + 300 };
    const refreshed = await aom.adapter.replays!('44987874');
    expect(refreshed.status).toBe('ok');
    if (refreshed.status !== 'ok') return;
    expect(refreshed.value.every((r) => r.availability === 'available' && !!r.sourceUrl)).toBe(
      true,
    );
  });
});

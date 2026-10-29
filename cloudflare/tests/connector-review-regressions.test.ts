import { runInDurableObject } from 'cloudflare:test';
import { describe, expect, it, vi } from 'vitest';
import { normalizeObservation, validObservation, type LobbyEvent } from '../src/domain/lobby';
import type { Match } from '../src/domain/play';
import { parseBarReplay } from '../src/games/bar';
import { parseGeneralsMatch } from '../src/games/generals-online';
import { gameAdapter } from '../src/games/registry';
import type { CatalogId, LobbyObservation, ProviderResult } from '../src/games/types';
import { LobbyStore } from '../src/storage/lobby-store';
import {
  act,
  action,
  current,
  fill,
  game,
  guild,
  harness,
  person,
  settings,
  stub,
} from './harness';
import barFixture from './fixtures/connectors/bar-v1.json';
import generalsFixture from './fixtures/connectors/generals-online-v1.json';

harness();

type CommunityInternals = {
  playStore: { lobbies: LobbyStore; saveMatch(match: Match): void };
};
const account = (user: string) => String(BigInt(user) - 100000000000000000n);
const nativeMatchId = '44987874';
const recordingSource =
  'https://fixture.blob.core.windows.net/recordings/fixture.rep?sig=fixture-new';

function config(id: CatalogId = 'aoe2', automatic = true, discovery = false) {
  const s = settings();
  s.games[0].catalogId = id;
  s.games[0].connector = {
    enabled: automatic,
    ...(discovery ? { autoAttach: { lobbyName: 'Review room' } } : {}),
  };
  s.queues[0].checkIn.enabled = false;
  s.queues[0].modules.predictions = true;
  s.queues[0].modules.gold = true;
  return s;
}
function observation(m: Match, phase: LobbyObservation['phase']): LobbyObservation {
  return {
    schemaVersion: 2,
    externalId: m.lobby?.externalId ?? '9001',
    nativeLobbyId: m.lobby?.externalId ?? '9001',
    name: 'Review room',
    phase,
    observedAt: Date.now(),
    startedAt: null,
    finishedAt: null,
    players: m.teams.flatMap((team, side) =>
      team.map((p) => ({
        profileId: account(p.id),
        name: p.name,
        team: String(side + 1),
      })),
    ),
    capacity: m.roster.length,
    map: null,
    source: 'review-fixture',
  };
}
function event(m: Match, evidence: LobbyObservation): LobbyEvent {
  return {
    schemaVersion: 2,
    communityId: guild,
    gameId: game,
    matchId: m.id,
    bindingId: m.lobby!.id,
    rosterVersion: m.lobby!.rosterVersion,
    adapterId: m.lobby!.adapterId,
    eventId: crypto.randomUUID(),
    observation: evidence,
  };
}
const accept = (m: Match, evidence: LobbyObservation) =>
  runInDurableObject(stub(), (instance) =>
    (instance as unknown as CommunityInternals).playStore.lobbies.accept(guild, event(m, evidence)),
  );
const snapshot = () => stub().operationalBackup(guild);
const captain = (m: Match) => ({ id: m.captains[0], name: 'Captain' });
const providerCalls = () =>
  vi
    .mocked(fetch)
    .mock.calls.map(([input]) => String(input instanceof Request ? input.url : input))
    .filter((url) => !url.startsWith('https://discord.com/'));
async function bound(id: CatalogId = 'aoe2', automatic = true) {
  const m = await fill(config(id, automatic));
  expect(await act('lobby_attach', 0, { matchId: m.id, reference: '9001' })).toMatchObject({
    ok: true,
  });
  return current();
}

describe('native identity compatibility', () => {
  it.each(['bar', 'zero_hour'] as const)(
    'preserves a %s match-only observation without inventing a lobby ID',
    (id) => {
      const now = Date.parse('2026-10-28T23:00:00Z');
      const decoded =
        id === 'bar'
          ? parseBarReplay(barFixture.replay, barFixture.replay.id, now)
          : parseGeneralsMatch(generalsFixture.match, generalsFixture.match.match_id, now);
      const normalized = normalizeObservation(decoded, 'fixture-generation');
      expect(validObservation(normalized, now)).toBe(true);
      expect(normalized.nativeLobbyId).toBeUndefined();
      expect(normalized.nativeMatchId).toBe(decoded.nativeMatchId);
      expect(normalized.externalId).toBe(
        id === 'bar' ? barFixture.replay.id : `match:${generalsFixture.match.match_id}`,
      );
      expect(normalized.replayReferences).toEqual(decoded.replayReferences);
      expect(normalized.bindingGeneration).toBe('fixture-generation');
    },
  );
  it('derives native lobby IDs only for the legacy observation format', () => {
    const legacy: LobbyObservation = {
      externalId: '9001',
      name: 'Legacy lobby',
      phase: 'open',
      observedAt: Date.now(),
      startedAt: null,
      finishedAt: null,
      players: [],
      capacity: 4,
      map: null,
      source: 'legacy',
    };
    expect(normalizeObservation(legacy).nativeLobbyId).toBe('9001');
    expect(normalizeObservation({ ...legacy, schemaVersion: 2 }).nativeLobbyId).toBeUndefined();
  });
});

describe('started observations with unknown timestamps', () => {
  it('stops auto-discovery and ignores a queued remake search after a start with no timestamp', async () => {
    const s = config('aoe2', true, true);
    const m = await fill(s);
    await runInDurableObject(stub(), async (instance, state) => {
      const play = (instance as unknown as CommunityInternals).playStore;
      const read = (id: string) =>
        JSON.parse(
          state.storage.sql
            .exec<{ config: string }>('SELECT config FROM matches WHERE id=?', id)
            .one().config,
        ) as Match;
      let candidate = observation(m, 'full');
      const discover = vi.fn(async () => ({ healthy: true, observations: [candidate] }));
      const adapter = {
        ...gameAdapter('aoe2')!,
        observe: async (): Promise<ProviderResult<LobbyObservation>> => ({ status: 'delayed' }),
      };
      const store = new LobbyStore(state, () => s, {
        adapter: () => adapter,
        availability: () => 'available',
        match: read,
        save: (next) => play.saveMatch(next),
        prompt: () => {},
        audit: () => {},
        discover,
      });
      await store.poll(guild);
      const attached = read(m.id);
      expect(attached.lobby).toMatchObject({ automatic: true, externalId: '9001' });
      expect(store.accept(guild, event(attached, observation(attached, 'started')))).toMatchObject({
        accepted: true,
      });
      expect(read(m.id).lobbySearch?.state).toBe('stopped');
      expect(
        state.storage.sql.exec("SELECT * FROM deadlines WHERE kind='lobby_discovery'").toArray(),
      ).toEqual([]);
      candidate = { ...candidate, externalId: '9002', nativeLobbyId: '9002' };
      // A previously queued alarm must re-check the phase before looking for a same-roster remake.
      state.storage.sql.exec(
        'INSERT OR REPLACE INTO deadlines VALUES(?,?,?,?)',
        `lobby-search:${m.id}`,
        'lobby_discovery',
        m.id,
        0,
      );
      await store.poll(guild);
      expect(discover).toHaveBeenCalledTimes(1);
      expect(read(m.id).lobby).toMatchObject({
        id: attached.lobby!.id,
        externalId: '9001',
        observation: { phase: 'started', startedAt: null },
      });
    });
  });
  it.each(['open', 'full'] as const)(
    'does not regress a start with no timestamp to %s',
    async (phase) => {
      const m = await bound();
      expect(await accept(m, observation(m, 'started'))).toMatchObject({ accepted: true });
      expect(await accept(m, observation(m, phase))).toMatchObject({
        accepted: false,
        reason: 'conflicting_evidence',
      });
      expect((await current()).lobby!.observation).toMatchObject({
        phase: 'started',
        startedAt: null,
      });
      expect((await snapshot()).tables.rating_changes).toHaveLength(0);
    },
  );
  it('pauses bet changes after captain confirmation without fabricating a timestamp or cutoff', async () => {
    const m = await bound();
    expect((await act('bet', 40, { matchId: m.id, side: 'alpha', stake: 50 })).ok).not.toBe(false);
    expect(await accept(m, observation(m, 'started'))).toMatchObject({ accepted: true });
    expect(
      await act('lobby_confirm', 0, { actor: captain(m), matchId: m.id, reference: m.lobby!.id }),
    ).toMatchObject({ ok: true });
    expect(await act('bet', 41, { matchId: m.id, side: 'beta', stake: 50 })).toMatchObject({
      ok: false,
    });
    expect(await act('bet_cancel', 40, { matchId: m.id })).toMatchObject({ ok: false });
    const backup = await snapshot();
    expect(backup.tables.bet_start_evidence).toHaveLength(0);
    expect(backup.tables.rating_changes).toHaveLength(0);
    expect(backup.tables.books[0].status).toBe('open');
    expect(backup.tables.bets).toHaveLength(1);
    expect((await current()).lobby!.observation!.startedAt).toBeNull();
  });
});

async function priorReplayObservation() {
  const m = await bound('aom', false);
  // A persisted observation may outlive automatic observation settings or acceptance approval.
  await runInDurableObject(stub(), (instance) => {
    m.lobby!.state = 'observed';
    m.lobby!.lastSuccessAt = Date.now();
    m.lobby!.observation = {
      ...observation(m, 'finished'),
      nativeMatchId,
      provider: 'worlds-edge',
      accountNamespace: 'worlds-edge:athens',
      bindingGeneration: m.lobby!.id,
      replayReferences: [
        {
          provider: 'worlds-edge:athens',
          matchId: nativeMatchId,
          availability: 'expired',
          expiresAt: Date.now() - 1000,
          sourceUrl: 'https://fixture.blob.core.windows.net/recordings/fixture.rep?sig=fixture-old',
        },
      ],
    };
    (instance as unknown as CommunityInternals).playStore.saveMatch(m);
  });
  return current();
}
function replayNetwork(beforeReply?: () => Promise<void>) {
  const original = vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    const url = new URL(input instanceof Request ? input.url : String(input));
    if (url.hostname === 'athens-live-api.worldsedgelink.com') {
      expect(url.pathname).toBe('/community/leaderboard/getReplayFiles');
      expect(url.searchParams.get('matchIDs')).toBe(`[${nativeMatchId}]`);
      await beforeReply?.();
      return Response.json({
        result: { code: 0, message: 'SUCCESS' },
        expiryUnix: Math.floor(Date.now() / 1000) + 900,
        replayFiles: [
          {
            matchhistory_id: nativeMatchId,
            profile_id: 100,
            datatype: 0,
            size: 12345,
            url: recordingSource,
          },
        ],
      });
    }
    return original(input, init);
  });
}

describe('independent replay-reference refresh', () => {
  it('refreshes expired metadata while automatic observations are off, preserving its generation and native identity', async () => {
    const m = await priorReplayObservation();
    replayNetwork();
    const status = await stub().connectorStatus(guild);
    expect(status.games[0].capabilities).toMatchObject({
      lobbyObservation: 'disabled',
      replayReferences: 'available',
    });
    expect(
      await stub().refreshLobbyReplays(
        guild,
        action('lobby_replays', 0, { actor: captain(m), matchId: m.id }),
      ),
    ).toMatchObject({ ok: true });
    const after = await current();
    expect(after.lobby).toMatchObject({
      id: m.lobby!.id,
      observation: { bindingGeneration: m.lobby!.id, nativeMatchId },
    });
    expect(after.lobby!.observation!.replayReferences).toMatchObject([
      { availability: 'available', matchId: nativeMatchId, sourceUrl: recordingSource },
    ]);
    expect(after.lobby!.observation!.replayReferences![0].expiresAt).toBeGreaterThan(Date.now());
    expect(providerCalls()).toHaveLength(1);
    expect(new URL(providerCalls()[0]).pathname).toBe('/community/leaderboard/getReplayFiles');
    expect((await snapshot()).tables.rating_changes).toHaveLength(0);
  });
  it('discards refresh results when a different binding replaces the request generation', async () => {
    const m = await priorReplayObservation();
    replayNetwork(async () => {
      expect(
        await act('lobby_attach', 0, { actor: captain(m), matchId: m.id, reference: '9002' }),
      ).toMatchObject({ ok: true });
    });
    expect(
      await stub().refreshLobbyReplays(
        guild,
        action('lobby_replays', 0, { actor: captain(m), matchId: m.id }),
      ),
    ).toMatchObject({
      ok: false,
      content: 'The lobby binding changed. Refresh its current status first.',
    });
    const after = await current();
    expect(after.lobby!.id).not.toBe(m.lobby!.id);
    expect(after.lobby!.externalId).toBe('9002');
    expect(after.lobby!.observation).toBeUndefined();
    expect(providerCalls()).toHaveLength(1);
  });
  it('honors an explicit replay capability switch without issuing a provider request', async () => {
    const m = await priorReplayObservation();
    const saved = await stub().read(guild);
    saved.settings.games[0].connector!.capabilities = { replayReferences: false };
    expect(
      await stub().save(guild, saved.revision, saved.settings, person(99).id, 'save'),
    ).toMatchObject({ ok: true });
    replayNetwork();
    expect(
      await stub().refreshLobbyReplays(
        guild,
        action('lobby_replays', 0, { actor: captain(m), matchId: m.id }),
      ),
    ).toMatchObject({ ok: false });
    expect(providerCalls()).toEqual([]);
    expect((await current()).lobby!.observation!.replayReferences![0].availability).toBe('expired');
  });
});

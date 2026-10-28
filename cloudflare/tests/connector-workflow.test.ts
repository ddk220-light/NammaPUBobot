import { env } from 'cloudflare:workers';
import { abortAllDurableObjects, runInDurableObject } from 'cloudflare:test';
import { describe, expect, it, vi } from 'vitest';
import { catalogIds, type CatalogId, type LobbyObservation } from '../src/games/types';
import { type LobbyEvent } from '../src/domain/lobby';
import type { Match } from '../src/domain/play';
import {
  harness,
  settings,
  setup,
  fill,
  act,
  action,
  current,
  stub,
  guild,
  game,
  person,
} from './harness';

harness();
const account = (user: string) => String(BigInt(user) - 100000000000000000n);
function config(id: CatalogId, automatic = false) {
  const s = settings();
  s.games[0].catalogId = id;
  s.games[0].connector = { enabled: automatic };
  s.queues[0].checkIn.enabled = false;
  s.queues[0].modules.predictions = true;
  s.queues[0].modules.gold = true;
  return s;
}
async function bound(id: CatalogId = 'aoe2', linked = true, automatic = true) {
  const m = await fill(config(id, automatic));
  if (linked)
    for (const p of m.roster) {
      expect(
        await stub().recordAction(
          guild,
          {
            id: crypto.randomUUID(),
            kind: 'link',
            gameId: game,
            userId: p.id,
            provider: 'aoe2',
            externalId: account(p.id),
            url: `https://www.aoe2companion.com/profile/${account(p.id)}`,
          },
          p.id,
          false,
        ),
      ).toMatchObject({ ok: true });
    }
  expect(await act('lobby_attach', 0, { matchId: m.id, reference: '9001' })).toMatchObject({
    ok: true,
  });
  return current();
}
function observation(m: Match, phase: LobbyObservation['phase'] = 'finished'): LobbyObservation {
  return {
    schemaVersion: 2,
    externalId: m.lobby!.externalId,
    nativeLobbyId: m.lobby!.externalId,
    nativeMatchId: '99001',
    platformLobbyId: '109775242664684177',
    name: 'Fixture lobby',
    phase,
    observedAt: Date.now(),
    startedAt: ['started', 'finished'].includes(phase) ? m.createdAt : null,
    finishedAt: phase === 'finished' ? Date.now() : null,
    players: m.teams.flatMap((team, side) =>
      team.map((p) => ({
        profileId: account(p.id),
        name: p.name,
        team: String(side + 1),
        selection: side === 0 ? 'Franks' : 'Mongols',
        selectionId: side === 0 ? '6' : '12',
        outcome: side === 0 ? ('win' as const) : ('loss' as const),
      })),
    ),
    capacity: m.roster.length,
    map: 'Nomad',
    source: 'fixture',
    confidence: 'provisional',
    edition: 'Age of Empires II: Definitive Edition',
    provider: 'aoe2companion',
    accountNamespace: 'aoe2:profile',
    bindingGeneration: m.lobby!.id,
    elapsedDuration: { value: 900, unit: 'seconds' },
    replayReferences: [
      { provider: 'aoe2companion', matchId: '99001', availability: 'unavailable' },
    ],
  };
}
function event(m: Match, o = observation(m)): LobbyEvent {
  return {
    schemaVersion: 2,
    communityId: guild,
    gameId: game,
    matchId: m.id,
    bindingId: m.lobby!.id,
    rosterVersion: m.lobby!.rosterVersion,
    adapterId: m.lobby!.adapterId,
    eventId: crypto.randomUUID(),
    observation: o,
  };
}
const jobs = () =>
  runInDurableObject(stub(), (_instance, state) =>
    state.storage.sql
      .exec("SELECT * FROM deadlines WHERE kind IN ('lobby','lobby_discovery')")
      .toArray(),
  );
const snapshot = () => stub().operationalBackup(guild);
const captain = (m: Match, side = 0) => ({ id: m.captains[side], name: 'Captain' });

describe.each(catalogIds)('%s manual connector journey', (id) => {
  it('retains matchmaking, declarations, manual betting and captain-confirmed statistics without provider access', async () => {
    const m = await bound(id, false, false);
    expect((await jobs()).length).toBe(0);
    const other = m.roster.find((p) => !m.captains.includes(p.id))!;
    expect(
      await act('lobby_facts', 0, {
        actor: { id: other.id, name: other.name },
        matchId: m.id,
        facts: { map: 'Unauthorized' },
      }),
    ).toMatchObject({ ok: false });
    expect(
      await act('lobby_facts', 0, {
        actor: { id: other.id, name: other.name },
        matchId: m.id,
        facts: { elapsedSeconds: 100 },
      }),
    ).toMatchObject({ ok: false });
    expect(
      await act('lobby_facts', 0, {
        actor: { id: other.id, name: other.name },
        matchId: m.id,
        facts: { selection: 'Declared faction' },
      }),
    ).toMatchObject({ ok: true });
    const shared = action('lobby_facts', 0, {
      actor: captain(m),
      matchId: m.id,
      facts: {
        map: 'Declared map',
        elapsedSeconds: 1200,
        recordingUrl: 'https://example.com/recording-reference',
      },
    });
    expect(await stub().play(guild, shared)).toMatchObject({ ok: true });
    expect(await stub().play(guild, shared)).toMatchObject({ ok: true });
    expect((await current()).lobby!.manualFacts?.recordingLinks).toEqual([
      'https://example.com/recording-reference',
    ]);
    expect((await current()).lobby!.observation).toBeUndefined();
    expect((await snapshot()).tables.profile_refs).toHaveLength(0);
    expect((await snapshot()).tables.played_selections).toHaveLength(0);
    expect(
      (await stub().statistics(guild, game, other.id, 0, true, other.id)).player?.civilizations,
    ).toEqual([]);
    // Merely attaching a manual lobby must not disable the existing manual prediction workflow.
    expect((await act('bet', 40, { matchId: m.id, side: 'alpha', stake: 50 })).ok).not.toBe(false);
    expect((await act('bet', 41, { matchId: m.id, side: 'beta', stake: 50 })).ok).not.toBe(false);
    expect((await act('bet_close', 0, { actor: captain(m), matchId: m.id })).ok).not.toBe(false);
    expect(
      await act('lobby_confirm', 0, { actor: captain(m), matchId: m.id, reference: m.lobby!.id }),
    ).toMatchObject({ ok: true });
    expect(
      await act('loss', 0, { actor: { id: other.id, name: other.name }, matchId: m.id }),
    ).toMatchObject({ ok: false });
    expect((await snapshot()).tables.rating_changes).toHaveLength(0);
    expect((await act('loss', 0, { actor: captain(m, 1), matchId: m.id })).ok).not.toBe(false);
    const finished = await current();
    expect(finished).toMatchObject({ state: 'finished', result: 'alpha' });
    expect(finished.lobby!.manualFacts).toMatchObject({
      map: 'Declared map',
      elapsedSeconds: 1200,
    });
    expect(finished.lobby!.observation).toBeUndefined();
    const stats = await stub().statistics(guild, game, other.id, 0, true, other.id);
    expect(stats.player?.civilizations).toMatchObject([
      { selection: 'Declared faction', games: 1, inferred: 0 },
    ]);
    const backup = await snapshot();
    expect(backup.tables.rating_changes).toHaveLength(4);
    expect(backup.tables.bet_start_evidence).toHaveLength(0);
    expect(backup.tables.books[0].status).toBe('settled');
    expect(backup.tables.profile_refs).toHaveLength(0);
    expect(await jobs()).toHaveLength(0);
    const providerCalls = vi
      .mocked(fetch)
      .mock.calls.map(([input]) => String(input instanceof Request ? input.url : input))
      .filter((url) => !url.startsWith('https://discord.com/'));
    expect(providerCalls).toEqual([]);
  });
});

describe('provider evidence in the real community Durable Object', () => {
  it.each(['aoe3', 'aoe4', 'aom', 'bar', 'zero_hour'] as const)(
    'keeps %s manual until acceptance/credentials are configured',
    async (id) => {
      const m = await bound(id, false, true);
      const availability = id === 'zero_hour' ? 'credentials_required' : 'acceptance_required';
      expect(m.lobby?.availability).toBe(availability);
      expect(await jobs()).toHaveLength(0);
      const status = await stub().connectorStatus(guild);
      expect(status.games[0].capabilities.lobbyObservation).toBe(availability);
      expect(await stub().connectorEnded(guild, event(m))).toMatchObject({
        accepted: false,
        reason: 'capability_unavailable',
      });
      expect((await current()).state).toBe('playing');
      expect((await snapshot()).tables.rating_changes).toHaveLength(0);
    },
  );
  it('prefills coherent results without changing ratings until the losing captain reports', async () => {
    const m = await bound();
    const e = event(m);
    expect(await stub().connectorEnded(guild, e)).toMatchObject({ accepted: true });
    expect((await current()).lobby).toMatchObject({
      rosterVerified: true,
      suggestedResult: 'alpha',
      prompted: true,
    });
    expect((await current()).result).toBeNull();
    expect((await snapshot()).tables.rating_changes).toHaveLength(0);
    expect(await jobs()).toHaveLength(0);
    expect(await stub().connectorEnded(guild, e)).toMatchObject({ accepted: false });
    const other = m.roster.find((p) => !m.captains.includes(p.id))!;
    expect(
      await act('loss', 0, { actor: { id: other.id, name: other.name }, matchId: m.id }),
    ).toMatchObject({ ok: false });
    expect((await act('loss', 0, { actor: captain(m, 1), matchId: m.id })).ok).not.toBe(false);
    expect((await current()).result).toBe('alpha');
    expect((await snapshot()).tables.rating_changes).toHaveLength(4);
  });
  it.each(['missing', 'inconsistent', 'draw-versus-win'] as const)(
    'does not prefill %s outcomes',
    async (kind) => {
      const m = await bound();
      const o = observation(m);
      if (kind === 'missing') delete o.players[0].outcome;
      if (kind === 'inconsistent') o.players.find((p) => p.team === '2')!.outcome = 'win';
      if (kind === 'draw-versus-win') o.players.find((p) => p.team === '2')!.outcome = 'draw';
      expect(await stub().connectorEnded(guild, event(m, o))).toMatchObject({ accepted: true });
      expect((await current()).lobby?.suggestedResult).toBeUndefined();
      expect((await current()).lobby?.prompted).toBe(false);
      expect((await snapshot()).tables.rating_changes).toHaveLength(0);
    },
  );
  it('persists provisional evidence across restart without betting cutoff until a captain confirms', async () => {
    const m = await bound('aoe2', false, true);
    const o = observation(m, 'finished');
    expect(await stub().connectorEnded(guild, event(m, o))).toMatchObject({ accepted: true });
    expect((await current()).lobby).toMatchObject({
      rosterVerified: false,
      observation: { confidence: 'provisional' },
    });
    expect((await snapshot()).tables.bet_start_evidence).toHaveLength(0);
    expect((await snapshot()).tables.books[0].status).toBe('open');
    expect((await act('bet', 40, { matchId: m.id, side: 'alpha', stake: 50 })).ok).not.toBe(false);
    await abortAllDurableObjects();
    const other = m.roster.find((p) => !m.captains.includes(p.id))!;
    expect(
      await act('lobby_confirm', 0, {
        actor: { id: other.id, name: other.name },
        matchId: m.id,
        reference: m.lobby!.id,
      }),
    ).toMatchObject({ ok: false });
    expect(
      await act('lobby_confirm', 0, {
        actor: captain(m),
        matchId: m.id,
        reference: crypto.randomUUID(),
      }),
    ).toMatchObject({ ok: false });
    expect(
      await act('lobby_confirm', 0, { actor: captain(m), matchId: m.id, reference: m.lobby!.id }),
    ).toMatchObject({ ok: true });
    expect((await snapshot()).tables.bet_start_evidence).toHaveLength(1);
    expect((await snapshot()).tables.books[0].status).toBe('refunded');
    expect((await snapshot()).tables.profile_refs).toHaveLength(0);
    expect((await snapshot()).tables.rating_changes).toHaveLength(0);
  });
  it('accepts schema2 unknown-start facts without fabricating a betting cutoff and normalizes legacy events', async () => {
    const m = await bound();
    const unknown = observation(m, 'finished');
    unknown.startedAt = null;
    unknown.finishedAt = null;
    expect(await stub().connectorEnded(guild, event(m, unknown))).toMatchObject({ accepted: true });
    expect((await snapshot()).tables.bet_start_evidence).toHaveLength(0);
    expect((await current()).lobby!.observation!.startedAt).toBeNull();
    const legacy: LobbyObservation = {
      externalId: m.lobby!.externalId,
      name: 'Legacy',
      phase: 'finished',
      observedAt: Date.now(),
      startedAt: m.createdAt,
      finishedAt: Date.now(),
      players: observation(m).players.map(({ profileId, name, team }) => ({
        profileId,
        name,
        team,
      })),
      capacity: 4,
      map: null,
      source: 'legacy-event',
    };
    expect(
      await stub().connectorEnded(guild, { ...event(m, legacy), schemaVersion: 1 }),
    ).toMatchObject({ accepted: true });
    expect((await current()).lobby!.observation).toMatchObject({
      schemaVersion: 2,
      bindingGeneration: m.lobby!.id,
      nativeLobbyId: m.lobby!.externalId,
    });
    expect((await current()).lobby?.suggestedResult).toBeUndefined();
  });
  it('rejects stale generation, delayed observations and cross-community evidence', async () => {
    const m = await bound();
    const e = event(m);
    expect(
      await stub().connectorEnded(guild, {
        ...e,
        observation: { ...e.observation, bindingGeneration: crypto.randomUUID() },
      }),
    ).toMatchObject({ accepted: false });
    expect(
      await stub().connectorEnded(guild, {
        ...e,
        observation: { ...e.observation, observedAt: Date.now() - 300001 },
      }),
    ).toMatchObject({ accepted: false });
    const other = '706320010368516299';
    expect(await stub(other).connectorEnded(other, e)).toMatchObject({ accepted: false });
    expect(await act('lobby_attach', 0, { matchId: m.id, reference: '9002' })).toMatchObject({
      ok: true,
    });
    expect((await current()).lobby!.id).not.toBe(m.lobby!.id);
    expect(await stub().connectorEnded(guild, e)).toMatchObject({ accepted: false });
    const after = await current();
    expect(after.lobby!.observation).toBeUndefined();
    expect((await snapshot()).tables.bet_start_evidence).toHaveLength(0);
  });
  it.each(['substitute', 'unlink', 'cancel'] as const)(
    'rejects observations after %s',
    async (change) => {
      const m = await bound();
      if (change === 'substitute')
        await act('force_sub', 99, {
          admin: true,
          matchId: m.id,
          target: { id: m.roster[0].id, name: m.roster[0].name },
          replacement: person(20),
        });
      if (change === 'unlink') await act('lobby_unlink', 0, { actor: captain(m), matchId: m.id });
      if (change === 'cancel') await act('recover_abort', 99, { admin: true, matchId: m.id });
      expect(await stub().connectorEnded(guild, event(m))).toMatchObject({ accepted: false });
      expect((await current()).lobby?.observation).toBeUndefined();
      expect((await snapshot()).tables.rating_changes).toHaveLength(0);
      expect(await jobs()).toHaveLength(0);
    },
  );
});

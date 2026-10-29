import { runInDurableObject } from 'cloudflare:test';
import { describe, expect, it, vi } from 'vitest';
import type { Env } from '../src/env';
import type { LobbyEvent } from '../src/domain/lobby';
import type { Match } from '../src/domain/play';
import type { LobbyObservation } from '../src/games/types';
import { decodeWc3Match, wc3ProfileUrl } from '../src/games/wc3';
import type { LobbyStore } from '../src/storage/lobby-store';
import fixtures from './fixtures/connectors/wc3/v1/contracts.json';
import { act, action, game, guild, harness, person, settings, setup, stub } from './harness';

harness();

type CommunityInternals = {
  env: Env;
  playStore: { env: Env; lobbies: LobbyStore };
};
const nativeId = fixtures.playerOngoing['original-ongoing-match-id'];
const finishedRecordId = fixtures.correlatedFinished.match.id;
const recordReference = `record:${finishedRecordId}`;
const tags = fixtures.playerOngoing.teams.flatMap((team) =>
  team.players.map((player) => player.battleTag),
);
const snapshot = () => stub().operationalBackup(guild);
async function match(id: string) {
  return (await stub().records(guild)).matches.find((candidate) => candidate.id === id)! as Match;
}
async function enableAccepted1v1() {
  await runInDurableObject(stub(), (instance) => {
    // Isolated bindings on both owners; never mutate the shared test environment.
    const scope = instance as unknown as CommunityInternals;
    const acceptance = {
      CONNECTOR_ACCEPTED_GAMES: 'wc3',
      CONNECTOR_ACCEPTED_CAPABILITIES: 'wc3.lobbyObservation:1',
    };
    scope.env = { ...scope.env, ...acceptance };
    scope.playStore.env = { ...scope.playStore.env, ...acceptance };
  });
}
async function newMatch(reference = nativeId) {
  const existing = new Set((await stub().records(guild)).matches.map((m) => m.id));
  for (let index = 0; index < 2; index++) expect((await act('add', index)).ok).not.toBe(false);
  const created = (await stub().records(guild)).matches.find((m) => !existing.has(m.id))! as Match;
  expect(created).toMatchObject({ state: 'playing', result: null });
  expect(created.roster).toHaveLength(2);
  expect(await act('lobby_attach', 0, { matchId: created.id, reference })).toMatchObject({
    ok: true,
  });
  return match(created.id);
}
async function bound(reference = nativeId) {
  const config = settings();
  config.games[0].catalogId = 'wc3';
  config.games[0].connector = { enabled: true };
  config.queues[0].size = 2;
  config.queues[0].checkIn.enabled = false;
  config.queues[0].modules.gold = true;
  config.queues[0].modules.predictions = true;
  expect(await setup(config)).toMatchObject({ ok: true });
  await enableAccepted1v1();
  for (let index = 0; index < 2; index++)
    expect(
      await stub().recordAction(
        guild,
        {
          id: crypto.randomUUID(),
          kind: 'link',
          gameId: game,
          userId: person(index).id,
          provider: 'w3champions',
          externalId: tags[index],
          url: wc3ProfileUrl(tags[index]),
        },
        person(index).id,
        false,
      ),
    ).toMatchObject({ ok: true });
  const linked = await snapshot();
  expect(linked.tables.profile_claims).toHaveLength(2);
  expect(linked.tables.profile_claims.every((claim) => claim.trust === 'declared')).toBe(true);
  for (const table of ['profiles', 'participants', 'matches', 'rating_changes', 'wallets'] as const)
    expect(linked.tables[table]).toEqual([]);
  const m = await newMatch(reference);
  expect(m.lobby).toMatchObject({
    adapterId: 'wc3',
    referenceKind: 'match',
    availability: 'available',
    state: 'pending',
  });
  return m;
}
function observation(m: Match, phase: 'started' | 'finished'): LobbyObservation {
  const row = structuredClone(
    phase === 'started' ? fixtures.playerOngoing : fixtures.correlatedFinished.match,
  );
  // Preserve captured identity, roster and result semantics, rebasing only the clock.
  row.startTime = new Date(m.createdAt - 10000).toISOString();
  if (phase === 'finished') {
    row.endTime = new Date(m.createdAt - 9000).toISOString();
    row.durationInSeconds = 1;
  }
  return {
    ...decodeWc3Match(row, phase),
    // The real observer retains the caller's alias while recording the native IDs separately.
    externalId: m.lobby!.externalId,
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
    adapterId: 'wc3',
    eventId: crypto.randomUUID(),
    observation: evidence,
  };
}
const accept = (input: LobbyEvent) =>
  runInDurableObject(stub(), (instance) =>
    (instance as unknown as CommunityInternals).playStore.lobbies.accept(guild, input),
  );
function losingCaptain(m: Match) {
  const loser = fixtures.correlatedFinished.match.teams
    .flatMap((team) => team.players)
    .find((player) => !player.won)!;
  const user = person(tags.indexOf(loser.battleTag));
  const side = m.teams.findIndex((team) => team.some((p) => p.id === user.id));
  expect(m.captains[side]).toBe(user.id);
  return user;
}
function expectedResult(m: Match) {
  return m.teams[0].some((player) => player.id === losingCaptain(m).id) ? 'beta' : 'alpha';
}
const prompts = async (matchId: string) =>
  (await snapshot()).tables.outbox.filter((row) => String(row.key).startsWith(`end:${matchId}:`));

describe('W3Champions evidence in the real community Durable Object', () => {
  it('records an accepted 1v1 lifecycle but changes ratings only after the losing captain reports', async () => {
    const m = await bound();
    const before = await snapshot();
    const ongoing = observation(m, 'started');
    expect(await accept(event(m, ongoing))).toEqual({ accepted: true });
    expect((await match(m.id)).lobby).toMatchObject({
      rosterVerified: true,
      prompted: false,
      observation: {
        schemaVersion: 2,
        referenceKind: 'match',
        nativeMatchId: nativeId,
        bindingGeneration: m.lobby!.id,
        phase: 'started',
        confidence: 'roster',
      },
    });
    expect((await match(m.id)).lobby!.observation!.nativeLobbyId).toBeUndefined();
    const started = await snapshot();
    expect(started.tables.bet_start_evidence).toHaveLength(1);
    expect(started.tables.played_selections).toHaveLength(2);
    expect(started.tables.rating_changes).toEqual([]);
    expect(started.tables.profiles).toEqual(before.tables.profiles);
    expect(await prompts(m.id)).toHaveLength(0);

    const completed = event(m, observation(m, 'finished'));
    expect(await stub().connectorEnded(guild, completed)).toEqual({ accepted: true });
    expect(await match(m.id)).toMatchObject({
      state: 'playing',
      result: null,
      lobby: {
        rosterVerified: true,
        suggestedResult: expectedResult(m),
        prompted: true,
        observation: {
          nativeMatchId: nativeId,
          providerIds: { recordId: finishedRecordId },
          elapsedDuration: { value: 1, unit: 'seconds' },
        },
      },
    });
    const pending = await snapshot();
    expect(pending.tables.rating_changes).toEqual([]);
    expect(pending.tables.profiles).toEqual(before.tables.profiles);
    expect(pending.tables.bet_start_evidence).toHaveLength(1);
    expect(await prompts(m.id)).toHaveLength(1);
    expect(await act('loss', 99, { matchId: m.id })).toMatchObject({ ok: false });
    expect((await snapshot()).tables.rating_changes).toEqual([]);

    const report = action('loss', 0, { matchId: m.id, actor: losingCaptain(m) });
    expect((await stub().play(guild, report)).ok).not.toBe(false);
    expect((await stub().play(guild, report)).ok).not.toBe(false);
    expect(await match(m.id)).toMatchObject({ state: 'finished', result: expectedResult(m) });
    const finished = await snapshot();
    expect(finished.tables.rating_changes).toHaveLength(2);
    expect(finished.tables.rating_changes.every((row) => row.match_id === m.id)).toBe(true);
    expect(
      finished.tables.rating_changes.filter((row) => Number(row.after) > Number(row.before)),
    ).toHaveLength(1);
    expect(
      finished.tables.rating_changes.filter((row) => Number(row.after) < Number(row.before)),
    ).toHaveLength(1);
    expect(finished.tables.participants.filter((row) => row.state === 'finished')).toHaveLength(2);
    for (let index = 0; index < 2; index++) {
      const stats = await stub().statistics(
        guild,
        game,
        person(index).id,
        0,
        true,
        person(index).id,
      );
      expect(stats.player?.civilizations).toMatchObject([
        { selection: index === 0 ? 'Human' : 'Undead', games: 1, inferred: 0 },
      ]);
    }
    const external = vi
      .mocked(fetch)
      .mock.calls.filter(
        ([input]) =>
          !String(input instanceof Request ? input.url : input).startsWith('https://discord.com/'),
      );
    expect(external).toEqual([]);
  });

  it('rejects duplicate, delayed and regressing events without repeating persistent side effects', async () => {
    const m = await bound();
    const ongoing = event(m, observation(m, 'started'));
    expect(await accept(ongoing)).toEqual({ accepted: true });
    expect(await accept(ongoing)).toEqual({ accepted: false, reason: 'duplicate_or_old' });
    const completed = event(m, observation(m, 'finished'));
    expect(await stub().connectorEnded(guild, completed)).toEqual({ accepted: true });
    const before = await snapshot();
    expect(await stub().connectorEnded(guild, completed)).toEqual({
      accepted: false,
      reason: 'duplicate_or_old',
    });
    expect(
      await stub().connectorEnded(guild, {
        ...completed,
        eventId: crypto.randomUUID(),
        observation: { ...completed.observation, observedAt: completed.observation.observedAt - 1 },
      }),
    ).toEqual({ accepted: false, reason: 'duplicate_or_old' });
    expect(await accept(event(m, observation(m, 'started')))).toEqual({
      accepted: false,
      reason: 'conflicting_evidence',
    });
    const after = await snapshot();
    for (const table of [
      'rating_changes',
      'profiles',
      'played_selections',
      'bet_start_evidence',
    ] as const)
      expect(after.tables[table]).toEqual(before.tables[table]);
    expect(await prompts(m.id)).toHaveLength(1);
    expect((await match(m.id)).lobby!.lastEventId).toBe(completed.eventId);
  });

  it.each([
    [nativeId, nativeId],
    [nativeId, recordReference],
    [recordReference, nativeId],
    [recordReference, recordReference],
  ])('rejects completed native-game reuse from %s through %s', async (first, reused) => {
    const m = await bound(first);
    const originalEvidence = observation(m, 'finished');
    expect(await stub().connectorEnded(guild, event(m, originalEvidence))).toEqual({
      accepted: true,
    });
    expect((await act('loss', 0, { matchId: m.id, actor: losingCaptain(m) })).ok).not.toBe(false);
    const next = await newMatch(reused);
    const duplicate = {
      ...originalEvidence,
      externalId: next.lobby!.externalId,
      observedAt: Date.now(),
    };
    // Still inside the allowed start window: rejection must be identity-based, not age-based.
    expect(duplicate.startedAt!).toBeGreaterThanOrEqual(next.createdAt - 120000);
    expect(await stub().connectorEnded(guild, event(next, duplicate))).toEqual({
      accepted: false,
      reason: 'already_bound',
    });
    expect((await match(next.id)).lobby!.observation).toBeUndefined();
    const after = await snapshot();
    expect(after.tables.rating_changes).toHaveLength(2);
    expect(after.tables.played_selections.filter((row) => row.match_id === next.id)).toEqual([]);
    expect(after.tables.bet_start_evidence).toHaveLength(1);
    expect(await prompts(next.id)).toHaveLength(0);
  });

  it.each(['started', 'finished'] as const)(
    'rejects missing and unaccepted modes on incoming %s evidence before writes',
    async (phase) => {
      const m = await bound();
      const before = await snapshot();
      for (const mode of [undefined, '4']) {
        const row =
          phase === 'started'
            ? structuredClone(fixtures.teamOngoing.matches[0])
            : structuredClone(fixtures.teamFinished.matches[0]);
        row.startTime = new Date(m.createdAt - 10000).toISOString();
        if (phase === 'finished') {
          row.endTime = new Date(m.createdAt - 9000).toISOString();
          row.durationInSeconds = 1;
        }
        const decoded = decodeWc3Match(row, phase);
        const evidence = { ...decoded, externalId: m.lobby!.externalId };
        if (mode === undefined) delete evidence.mode;
        else evidence.mode = mode;
        const input = event(m, evidence);
        const result =
          phase === 'finished' ? await stub().connectorEnded(guild, input) : await accept(input);
        expect(result).toEqual({
          accepted: false,
          reason: mode ? 'capability_unavailable' : 'unsupported_mode',
        });
      }
      const after = await snapshot();
      for (const table of [
        'profiles',
        'rating_changes',
        'played_selections',
        'bet_start_evidence',
      ] as const)
        expect(after.tables[table]).toEqual(before.tables[table]);
      expect((await match(m.id)).lobby!.observation).toBeUndefined();
      expect(await prompts(m.id)).toHaveLength(0);
    },
  );

  it.each(['participants', 'teams'] as const)(
    'keeps mismatched %s provisional and prevents captain confirmation from driving start',
    async (mismatch) => {
      const m = await bound();
      const evidence = observation(m, 'started');
      if (mismatch === 'participants') evidence.players[0].profileId = 'Unrelated#1234';
      else evidence.players[1].team = evidence.players[0].team;
      expect(await accept(event(m, evidence))).toEqual({ accepted: true });
      expect((await match(m.id)).lobby).toMatchObject({
        rosterVerified: false,
        prompted: false,
        observation: { confidence: 'provisional' },
      });
      expect(
        await act('lobby_confirm', 0, {
          actor: { id: m.captains[0], name: 'Captain' },
          matchId: m.id,
          reference: m.lobby!.id,
        }),
      ).toMatchObject({ ok: false, content: expect.stringContaining('participants and teams') });
      expect((await match(m.id)).lobby!.confirmedBy).toBeUndefined();
      const after = await snapshot();
      expect(after.tables.bet_start_evidence).toEqual([]);
      expect(after.tables.played_selections).toEqual([]);
      expect(after.tables.rating_changes).toEqual([]);
      expect(after.tables.books[0].status).toBe('open');
      expect(await prompts(m.id)).toHaveLength(0);
      expect((await act('bet', 40, { matchId: m.id, side: 'alpha', stake: 50 })).ok).not.toBe(
        false,
      );
    },
  );
});

import { env, exports } from 'cloudflare:workers';
import { runInDurableObject, abortAllDurableObjects, runDurableObjectAlarm } from 'cloudflare:test';
import { describe, expect, it, vi } from 'vitest';
import app from '../src/worker';
import { LobbyStore } from '../src/storage/lobby-store';
import { type Match } from '../src/domain/play';
import { discoverLobby, type DiscoveryTarget } from '../src/domain/lobby-discovery';
import { AoE2LobbySnapshot } from '../src/games/aoe2-feed';
import { type LobbyObservation } from '../src/games/types';
import {
  harness,
  settings,
  setup,
  fill,
  act,
  current,
  stub,
  guild,
  game,
  queue,
  person,
} from './harness';

harness();
const config = () => {
  const s = settings();
  s.games[0].catalogId = 'aoe2';
  s.games[0].connector = { enabled: true, autoAttach: { lobbyName: 'NammaNomad' } };
  s.queues[0].checkIn.enabled = false;
  return s;
};
const lobby = (id = '9001', count = 4): LobbyObservation => ({
  externalId: id,
  name: 'NammaNomad',
  phase: count === 4 ? 'full' : 'open',
  observedAt: Date.now(),
  startedAt: null,
  finishedAt: null,
  capacity: 4,
  players: Array.from({ length: count }, (_, i) => ({
    profileId: String(100 + i),
    name: `Player ${i}`,
    team: i < 2 ? '1' : '2',
  })),
  map: 'Nomad',
  source: 'aoe2companion-lobbies',
});
const target = (id = 'match'): DiscoveryTarget => ({
  id,
  name: 'NammaNomad',
  profiles: [0, 1, 2, 3].map((i) => ({
    userId: person(i).id,
    profileId: null,
    side: i < 2 ? 0 : 1,
  })),
});
const events = (o: LobbyObservation) => [
  {
    type: 'lobbyAdded',
    data: { matchId: o.externalId, name: o.name, totalSlotCount: o.capacity, blockedSlotCount: 0 },
  },
  ...o.players.map((p, slot) => ({
    type: 'slotAdded',
    data: { matchId: o.externalId, slot, ...p },
  })),
];
const feed = () => env.AOE2_LOBBIES.getByName(env.DISCORD_APPLICATION_ID!);
function network() {
  const original = vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    const url = String(input instanceof Request ? input.url : input);
    if (url.includes('socket.aoe2companion.com')) {
      const pair = new WebSocketPair();
      pair[1].accept();
      return new Response(null, { status: 101, webSocket: pair[0] });
    }
    if (url.includes('data.aoe2companion.com')) return new Response(null, { status: 404 });
    return original(input, init);
  });
}
async function publish(observations: LobbyObservation[]) {
  await feed().discover('NammaNomad');
  await runInDurableObject(feed(), (instance) => {
    const snapshot: AoE2LobbySnapshot = (instance as any).snapshot;
    snapshot.clear();
    snapshot.consume(observations.flatMap(events), new Set(['name:nammanomad']), Date.now() - 3000);
  });
}
async function poll() {
  await runInDurableObject(stub(), (_i, state) =>
    state.storage.sql.exec("UPDATE deadlines SET due=0 WHERE kind IN ('lobby','lobby_discovery')"),
  );
  await stub().pollLobbies(guild);
}
const jobs = () =>
  runInDurableObject(stub(), (_i, s) =>
    s.storage.sql.exec("SELECT * FROM deadlines WHERE kind='lobby_discovery'").toArray(),
  );

describe('legacy name/full/count discovery with ambiguity checks', () => {
  it('attaches one full matching lobby without requiring profile links', () => {
    expect(
      discoverLobby('match', [target()], [{ ...lobby(), name: ' nammaNOMAD ' }], new Set()).attach
        ?.externalId,
    ).toBe('9001');
    expect(discoverLobby('match', [target()], [lobby('9001', 2)], new Set())).toMatchObject({
      candidate: { phase: 'open' },
      attach: undefined,
    });
  });
  it.each([
    { name: 'Different lobby' },
    { capacity: null },
    { capacity: 8 },
    { phase: 'started', startedAt: Date.now() },
    { observedAt: Date.now() - 100000 },
    { players: [...lobby().players, lobby().players[0]] },
  ])('rejects unsupported or stale evidence %j', (change) => {
    expect(
      discoverLobby('match', [target()], [{ ...lobby(), ...change } as LobbyObservation], new Set())
        .attach,
    ).toBeUndefined();
  });
  it('does not choose among same-name lobbies, concurrent unlinked matches, or claimed lobbies', () => {
    expect(discoverLobby('match', [target()], [lobby(), lobby('9002')], new Set()).state).toBe(
      'ambiguous',
    );
    expect(discoverLobby('match', [target(), target('other')], [lobby()], new Set()).state).toBe(
      'ambiguous',
    );
    expect(
      discoverLobby('match', [target()], [lobby()], new Set(['9001'])).candidate,
    ).toBeUndefined();
  });
  it('uses declared identities to disambiguate and rejects conflicting known teams', () => {
    const t = target();
    t.profiles[0].profileId = '100';
    expect(discoverLobby('match', [t, target('other')], [lobby()], new Set()).attach).toBeDefined();
    t.profiles[1].profileId = '102';
    expect(discoverLobby('match', [t], [lobby()], new Set()).attach).toBeUndefined();
    t.profiles[1].profileId = '999';
    expect(discoverLobby('match', [t], [lobby()], new Set()).attach).toBeUndefined();
  });
  it('retains name subscriptions across deltas, rejects unknown capacity, and drops renamed lobbies', () => {
    const s = new AoE2LobbySnapshot(),
      now = Date.now(),
      names = new Set(['name:nammanomad']);
    s.consume(events(lobby('9001', 2)), names, now - 3000);
    s.consume([{ type: 'lobbyUpdated', data: { matchId: 9001, mapName: 'Nomad' } }], names, now);
    expect(s.named('NammaNomad', now)[0].players).toHaveLength(2);
    s.consume([{ type: 'lobbyUpdated', data: { matchId: 9001, name: 'Other' } }], names, now);
    expect(s.named('NammaNomad', now)).toEqual([]);
    s.consume(events({ ...lobby(), capacity: null }), names, now - 3000);
    expect(s.named('NammaNomad', now)[0].phase).toBe('open');
    s.clear();
    expect(s.named('NammaNomad', now)).toEqual([]);
  });
});

describe('automatic lobby lifecycle and manual priority', () => {
  it('shows a filling lobby, attaches when full, and never interprets fullness as a start/result', async () => {
    network();
    await fill(config());
    expect(await jobs()).toHaveLength(1);
    await publish([lobby('9001', 2)]);
    await poll();
    let m = await current();
    expect(m.lobby).toBeUndefined();
    expect(m.lobbySearch?.candidate?.players).toHaveLength(2);
    await publish([lobby()]);
    await poll();
    m = await current();
    const binding = m.lobby!.id;
    expect(m.lobby).toMatchObject({
      automatic: true,
      externalId: '9001',
      observation: { phase: 'full', startedAt: null },
      prompted: false,
    });
    expect(m.result).toBeNull();
    expect(m.state).toBe('playing');
    await poll();
    expect((await current()).lobby?.id).toBe(binding);
    expect((await stub().records(guild)).changes).toEqual([]);
    const frames = vi
      .mocked(fetch)
      .mock.calls.filter(([url]) => String(url).includes('socket.aoe2companion.com'));
    expect(frames).toHaveLength(1); // attaching an already observed ID must not disrupt the shared feed
  });
  it('lets any roster player attach a different ID and pins it against automatic replacement', async () => {
    network();
    let m = await fill(config());
    const player = m.roster.find((p) => !m.captains.includes(p.id))!;
    expect(
      await act('lobby_attach', 99, {
        actor: { id: player.id, name: player.name },
        reference: '8123',
      }),
    ).toMatchObject({ ok: true });
    m = await current();
    expect(m.lobby).toMatchObject({ externalId: '8123', automatic: false });
    expect(await jobs()).toHaveLength(0);
    await publish([lobby()]);
    await poll();
    expect((await current()).lobby?.externalId).toBe('8123');
    expect(await act('lobby_attach', 99, { matchId: m.id, reference: '8124' })).toMatchObject({
      ok: false,
    });
    expect(
      await act('lobby_attach', 99, {
        actor: { id: player.id, name: player.name },
        matchId: m.id,
        reference: '8124',
        channelId: '706320010368516299',
      }),
    ).toMatchObject({ ok: false });
  });
  it('pins a player-confirmed automatic ID and also permits manually overriding it', async () => {
    network();
    await fill(config());
    await publish([lobby()]);
    await poll();
    const before = await current();
    await act('lobby_attach', 0, { reference: '9001' });
    expect((await current()).lobby).toMatchObject({ id: before.lobby!.id, automatic: false });
    expect(await jobs()).toHaveLength(0);
    await act('lobby_attach', 1, { reference: '9002' });
    expect((await current()).lobby?.externalId).toBe('9002');
  });
  it('respects unlink before discovery and across restart', async () => {
    network();
    const m = await fill(config());
    await act('lobby_unlink', 99, { actor: { id: m.captains[0], name: 'Captain' } });
    expect(await jobs()).toHaveLength(0);
    await abortAllDurableObjects();
    await publish([lobby()]);
    await poll();
    expect((await current()).lobby).toBeUndefined();
    expect((await current()).lobbyAutoDisabled).toBe(true);
  });
  it('survives restart with a durable search deadline and name lease', async () => {
    network();
    await fill(config());
    await publish([]);
    await poll();
    await abortAllDurableObjects();
    expect(await jobs()).toHaveLength(1);
    expect((await feed().status()).tracked).toBe(1);
    await publish([lobby()]);
    await poll();
    expect((await current()).lobby?.externalId).toBe('9001');
  });
  it('replaces an automatic pregame lobby for a same-player remake, but leaves explicit links alone', async () => {
    network();
    await fill(config());
    await publish([lobby()]);
    await poll();
    await publish([lobby('9002')]);
    await poll();
    expect((await current()).lobby?.externalId).toBe('9002');
    await act('lobby_attach', 0, { reference: '9002' });
    await publish([lobby('9003')]);
    await poll();
    expect((await current()).lobby?.externalId).toBe('9002');
  });
  it('keeps the full discovery roster when slot removals precede a remake', async () => {
    network();
    await fill(config());
    await publish([lobby()]);
    await poll();
    await publish([lobby('9001', 1)]);
    await poll();
    expect((await current()).lobby?.observation?.players).toHaveLength(1);
    await publish([lobby('9002')]);
    await poll();
    expect((await current()).lobby?.externalId).toBe('9002');
  });
  it('uses the durable alarm for discovery and stops automatic replacement on verified start', async () => {
    network();
    await fill(config());
    await publish([lobby()]);
    await runDurableObjectAlarm(stub());
    const m = await current();
    expect(m.lobby?.externalId).toBe('9001');
    const fallback = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      if (String(input).includes('data.aoe2companion.com'))
        return Response.json({
          matchId: 9001,
          started: new Date(m.createdAt).toISOString(),
          finished: null,
          teams: [
            { teamId: 1, players: lobby().players.slice(0, 2) },
            { teamId: 2, players: lobby().players.slice(2) },
          ],
        });
      return fallback(input, init);
    });
    await publish([lobby('9002')]);
    await poll();
    expect((await current()).lobby).toMatchObject({
      externalId: '9001',
      observation: { phase: 'started' },
    });
    expect(await jobs()).toHaveLength(0);
    expect((await current()).result).toBeNull();
  });
  it('a noncaptain’s explicit ID wins while a full automatic candidate is in flight', async () => {
    const m = await fill(config());
    const player = m.roster.find((p) => !m.captains.includes(p.id))!;
    await runInDurableObject(stub(), async (instance, state) => {
      const read = (id: string): Match =>
        JSON.parse(
          state.storage.sql
            .exec<{ config: string }>('SELECT config FROM matches WHERE id=?', id)
            .toArray()[0].config,
        );
      const store = new LobbyStore(state, () => config(), {
        availability: () => 'available',
        match: read,
        save: (match) => (instance as any).playStore.saveMatch(match),
        prompt: () => {},
        audit: () => {},
        discover: async () => {
          await Promise.resolve();
          store.action(read(m.id), {
            id: crypto.randomUUID(),
            kind: 'lobby_attach',
            actor: { id: player.id, name: player.name },
            channelId: m.queue.channelId,
            admin: false,
            reference: '8888',
          });
          return { healthy: true, observations: [lobby()] };
        },
      });
      await store.poll(guild);
    });
    expect((await current()).lobby).toMatchObject({ externalId: '8888', automatic: false });
    expect(await jobs()).toHaveLength(0);
  });
  it('re-evaluates current linked profiles before attaching a candidate', async () => {
    network();
    const m = await fill(config());
    await stub().recordAction(
      guild,
      {
        id: crypto.randomUUID(),
        kind: 'link',
        gameId: game,
        userId: m.roster[0].id,
        provider: 'aoe2',
        externalId: '9999',
        url: 'https://www.aoe2companion.com/profile/9999',
      },
      m.roster[0].id,
      false,
    );
    await publish([lobby()]);
    await poll();
    expect((await current()).lobby).toBeUndefined();
  });
  it('keeps provisional name/count evidence from closing bets until a captain confirms the binding', async () => {
    network();
    const s = config();
    s.queues[0].modules.predictions = s.queues[0].modules.gold = true;
    await fill(s);
    await publish([lobby()]);
    await poll();
    const m = await current(),
      fallback = vi.mocked(fetch).getMockImplementation()!;
    expect((await act('bet', 40, { matchId: m.id, side: 'alpha', stake: 50 })).ok).not.toBe(false);
    expect((await act('bet', 41, { matchId: m.id, side: 'beta', stake: 50 })).ok).not.toBe(false);
    await new Promise((resolve) => setTimeout(resolve, 5));
    const startedAt = Date.now();
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      if (String(input).includes('data.aoe2companion.com'))
        return Response.json({
          matchId: 9001,
          started: new Date(startedAt).toISOString(),
          finished: new Date().toISOString(),
          teams: m.teams.map((team, side) => ({
            teamId: side + 1,
            players: team.map((p) => ({
              profileId: String(BigInt(p.id) - 100000000000000000n),
              name: p.name,
              civName: 'Franks',
              won: side === 0,
            })),
          })),
        });
      return fallback(input, init);
    });
    await poll();
    expect((await current()).lobby).toMatchObject({
      observation: { phase: 'finished' },
      prompted: false,
      rosterVerified: false,
    });
    expect((await current()).result).toBeNull();
    const provisional = await stub().operationalBackup(guild);
    expect(provisional.tables.books[0].status).toBe('open');
    expect(provisional.tables.profile_refs).toHaveLength(0);
    expect(provisional.tables.bet_start_evidence).toHaveLength(0);
    expect(
      await act('lobby_confirm', 99, {
        actor: { id: m.captains[0], name: 'Captain' },
        matchId: m.id,
        reference: m.lobby!.id,
      }),
    ).toMatchObject({ ok: true });
    const before = await stub().operationalBackup(guild);
    expect(before.tables.books[0].status).toBe('frozen');
    expect(before.tables.rating_changes).toHaveLength(0);
    expect((await act('loss', 99, { actor: { id: m.captains[0], name: 'Captain' } })).ok).not.toBe(
      false,
    );
    expect((await current()).state).toBe('finished');
    const after = await stub().operationalBackup(guild);
    expect(after.tables.played_selections).toHaveLength(0);
    expect(after.tables.profile_refs).toHaveLength(0);
    expect(after.tables.books[0].status).toBe('settled');
  });
  it('keeps captain reporting authoritative after auto-attaching and stops all lobby jobs on abort', async () => {
    network();
    const s = config();
    s.queues[0].modules.predictions = s.queues[0].modules.gold = true;
    await fill(s);
    await publish([lobby()]);
    await poll();
    const m = await current(),
      noncaptain = m.roster.find((p) => !m.captains.includes(p.id))!;
    expect(
      await act('loss', 99, { actor: { id: noncaptain.id, name: noncaptain.name } }),
    ).toMatchObject({ ok: false });
    await act('abort', 99, { admin: true, matchId: m.id });
    expect(await jobs()).toHaveLength(0);
    expect((await current()).state).toBe('aborted');
  });
  it('never attaches one lobby to two concurrent matches', async () => {
    network();
    await setup(config());
    for (let n = 0; n < 8; n++) await act('add', n);
    await publish([lobby()]);
    await poll();
    const matches = (await stub().records(guild)).matches;
    expect(matches).toHaveLength(2);
    expect(matches.every((m) => !m.lobby && m.lobbySearch?.state === 'ambiguous')).toBe(true);
    await act('lobby_attach', 0, { reference: '9001' });
    expect(await act('lobby_attach', 4, { reference: '9001' })).toMatchObject({ ok: false });
  });
  it('stops polling when disabled and does not search during check-in', async () => {
    network();
    const s = config();
    s.queues[0].checkIn.enabled = true;
    await fill(s);
    expect(await jobs()).toHaveLength(0);
    for (let n = 0; n < 4; n++) await act('ready', n);
    expect(await jobs()).toHaveLength(1);
    s.games[0].connector!.enabled = false;
    await stub().save(guild, 1, s, person(99).id, 'save');
    expect(await jobs()).toHaveLength(0);
  });
  it('times out search after 90 minutes and preserves manual play during feed outages', async () => {
    await fill(config());
    await poll();
    expect((await current()).lobbySearch?.state).toBe('unavailable');
    await runInDurableObject(stub(), (_i, s) => {
      const row = s.storage.sql.exec<{ config: string }>('SELECT config FROM matches').toArray()[0];
      const m = JSON.parse(row.config);
      m.createdAt = Date.now() - 91 * 60000;
      m.playingAt = m.createdAt;
      s.storage.sql.exec('UPDATE matches SET config=?', JSON.stringify(m));
    });
    await poll();
    expect(await jobs()).toHaveLength(0);
    expect((await current()).state).toBe('playing');
  });
});

describe('bounded, audited staging feature activation', () => {
  it('previews without writes, changes only selected AoE2 settings, and is repeat-safe', async () => {
    const s = config();
    s.games[0].connector = { enabled: false };
    await setup(s);
    const before = await stub().operationalBackup(guild);
    expect(await stub().enableAoE2Features(guild, 1, [queue], 'NammaNomad', false)).toMatchObject({
      ok: true,
      applied: false,
      revision: 1,
    });
    expect((await stub().operationalBackup(guild)).tables).toEqual(before.tables);
    expect(await stub().enableAoE2Features(guild, 1, [queue], 'NammaNomad', true)).toMatchObject({
      ok: true,
      applied: true,
      revision: 2,
    });
    const after = await stub().read(guild);
    expect(after.settings.games[0].connector).toEqual({
      enabled: true,
      autoAttach: { lobbyName: 'NammaNomad' },
    });
    expect(after.settings.queues[0].modules).toEqual({
      gold: true,
      predictions: true,
      maps: true,
      stories: true,
      civs: true,
      quiz: false,
    });
    expect(await stub().enableAoE2Features(guild, 2, [queue], 'NammaNomad', true)).toMatchObject({
      ok: true,
      applied: false,
      revision: 2,
    });
    const backup = await stub().operationalBackup(guild);
    for (const table of ['matches', 'wallets', 'gold_ledger', 'profile_refs'])
      expect(backup.tables[table]).toEqual(before.tables[table]);
    expect((await stub().audit(guild))[0]).toMatchObject({
      actor_id: 'operator',
      action: 'enable_aoe2_features',
    });
  });
  it('rejects stale revision, non-AoE2/disabled/unknown queues, active games, and recovery copies', async () => {
    await setup(config());
    expect((await stub().enableAoE2Features(guild, 0, [queue], 'NammaNomad', true)).ok).toBe(false);
    expect(
      (await stub().enableAoE2Features(guild, 1, [crypto.randomUUID()], 'NammaNomad', true)).ok,
    ).toBe(false);
    const backup = await stub().operationalBackup(guild),
      copy = env.COMMUNITIES.getByName(`recovery:features:${guild}`);
    await copy.operationalRestore(guild, {
      ...backup,
      sourceApplicationId: backup.sourceApplicationId!,
    });
    expect((await copy.enableAoE2Features(guild, 1, [queue], 'NammaNomad', true)).ok).toBe(false);
    for (let n = 0; n < 4; n++) await act('add', n);
    expect((await stub().enableAoE2Features(guild, 1, [queue], 'NammaNomad', true)).ok).toBe(false);
  });
  it.each(['disabled', 'other-game'])(
    'rejects %s queues without changing records',
    async (kind) => {
      const s = config();
      if (kind === 'disabled') s.queues[0].enabled = false;
      else {
        s.games[0].catalogId = null;
        delete s.games[0].connector;
      }
      await setup(s);
      const before = await stub().operationalBackup(guild);
      expect((await stub().enableAoE2Features(guild, 1, [queue], 'NammaNomad', true)).ok).toBe(
        false,
      );
      expect((await stub().operationalBackup(guild)).tables).toEqual(before.tables);
    },
  );
  it('rejects production activation and arbitrary settings even with the operator token', async () => {
    const make = (body: object) =>
      new Request(`https://setup.example/ops/communities/${guild}/features/aoe2`, {
        method: 'POST',
        headers: { Authorization: 'Bearer feature-test-ops', 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
    const body = { expectedRevision: 1, queueIds: [queue], lobbyName: 'NammaNomad', apply: true };
    expect(
      (
        await app.fetch(make(body), {
          ...env,
          APP_ENV: 'production',
          OPS_TOKEN: 'feature-test-ops',
        })
      ).status,
    ).toBe(403);
    expect(
      (await app.fetch(make({ ...body, settings: {} }), { ...env, OPS_TOKEN: 'feature-test-ops' }))
        .status,
    ).toBe(400);
  });
  it('does not expose unauthenticated feature mutations or discovery diagnostics', async () => {
    expect(
      (
        await exports.default.fetch(
          new Request(`https://setup.example/ops/communities/${guild}/features/aoe2`, {
            method: 'POST',
            body: '{}',
          }),
        )
      ).status,
    ).toBe(403);
    expect(
      (await exports.default.fetch('https://setup.example/ops/aoe2/discovery?name=NammaNomad'))
        .status,
    ).toBe(403);
  });
});

import { env } from 'cloudflare:workers';
import { describe, it, expect, vi } from 'vitest';
import { runInDurableObject, abortAllDurableObjects, runDurableObjectAlarm } from 'cloudflare:test';
import {
  harness,
  settings,
  fill,
  current,
  act,
  stub,
  guild,
  game,
  person,
  action,
} from './harness';
import { CivStore, type CivRound } from '../src/storage/civ-store';
import { EconomyStore } from '../src/storage/economy-store';
import { LobbyStore } from '../src/storage/lobby-store';
import { inferLinks, nameSimilarity, type IdentityPlayer } from '../src/domain/identity';
import { observeGamePlayers, selectionStats } from '../src/storage/game-records';
import { AoE2LobbySnapshot } from '../src/games/aoe2-feed';
import { selectionPool, aoe2Civs } from '../src/games/selections';
import type { LobbyObservation } from '../src/games/types';
import type { Match } from '../src/domain/play';
harness();
const config = () => {
  const s = settings();
  s.games[0].catalogId = 'aoe2';
  s.games[0].connector = { enabled: true };
  s.queues[0].checkIn.enabled = false;
  s.queues[0].modules.civs = true;
  s.queues[0].modules.gold = true;
  s.queues[0].modules.predictions = true;
  return s;
};
const round = (id: string) =>
  runInDurableObject(stub(), (_i, state) => new CivStore(state.storage.sql, () => {}).get(id)!);
const savedCard = (key: string) =>
  runInDurableObject(stub(), (_i, state) =>
    JSON.parse(
      state.storage.sql
        .exec<{ payload: string }>('SELECT payload FROM outbox WHERE key=?', key)
        .toArray()[0].payload,
    ),
  );
const observation = (m: Match, startedAt = Date.now() - 1000): LobbyObservation => ({
  externalId: '9001',
  name: 'Fixture game',
  phase: 'started',
  observedAt: Date.now(),
  startedAt,
  finishedAt: null,
  players: m.teams.flatMap((t, side) =>
    t.map((p) => ({
      profileId: String(BigInt(p.id) - 100000000000000000n),
      name: p.name,
      team: String(side + 1),
      selection: side ? 'Mongols' : 'Franks',
    })),
  ),
  capacity: 4,
  map: 'Arabia',
  source: 'test-fixture',
});
async function attach(m: Match) {
  await act('lobby_attach', 99, { admin: true, matchId: m.id, reference: '9001' });
  return current();
}
async function accept(m: Match, o: LobbyObservation) {
  const s = await stub().read(guild);
  return runInDurableObject(stub(), (_i, state) =>
    new LobbyStore(state, () => s.settings, {
      availability: () => 'available',
      match: (id) =>
        JSON.parse(
          state.storage.sql
            .exec<{ config: string }>('SELECT config FROM matches WHERE id=?', id)
            .toArray()[0].config,
        ),
      save: (m) =>
        state.storage.sql.exec('UPDATE matches SET config=? WHERE id=?', JSON.stringify(m), m.id),
      prompt: () => {},
      audit: () => {},
      started: (m, o, id) => new EconomyStore(state.storage.sql).startObserved(m, o.startedAt!, id),
    }).accept(guild, {
      schemaVersion: 1,
      communityId: guild,
      gameId: game,
      matchId: m.id,
      bindingId: m.lobby!.id,
      rosterVersion: m.rosterVersion ?? 0,
      adapterId: 'aoe2',
      eventId: crypto.randomUUID(),
      observation: o,
    }),
  );
}
describe('durable civilization picker', () => {
  it('starts from the player’s active match without requiring an internal ID', async () => {
    const m = await fill(config());
    expect((await act('civ_start', 0)).ok).not.toBe(false);
    expect((await round(m.id)).status).toBe('open');
  });
  it('keeps choices private, rejects collisions, allows unlimited Random and reveals once', async () => {
    const m = await fill(config());
    await act('civ_start', 0, { matchId: m.id });
    let r = await round(m.id);
    expect(r.options).toHaveLength(12);
    expect(new Set(r.options).size).toBe(12);
    expect(r.options.filter((x) => aoe2Civs.base.includes(x))).toHaveLength(8);
    await act('civ_claim', 0, { matchId: m.id, generation: r.generation, choice: 0 });
    let card = await savedCard(`civ:${m.id}`);
    expect(card.embeds[0].description).toContain('Picked ✓');
    expect(card.embeds[0].description).not.toContain(r.options[0]);
    expect(card.components.flatMap((x: any) => x.components).every((b: any) => !b.disabled)).toBe(
      true,
    );
    expect(
      await act('civ_claim', 1, { matchId: m.id, generation: r.generation, choice: 0 }),
    ).toMatchObject({ ok: false });
    expect(
      await act('civ_claim', 0, { matchId: m.id, generation: r.generation, choice: 1 }),
    ).toMatchObject({ ok: false });
    await Promise.all(
      [1, 2, 3].map((n) =>
        act('civ_claim', n, { matchId: m.id, generation: r.generation, choice: 12 }),
      ),
    );
    r = await round(m.id);
    expect(r.status).toBe('closed');
    card = await savedCard(`civ:${m.id}`);
    expect(card.embeds[0].description).toContain(r.options[0]);
    expect(card.components.flatMap((x: any) => x.components).every((b: any) => b.disabled)).toBe(
      true,
    );
    const stats = await stub().statistics(guild, game, person(0).id, 0, true, person(0).id);
    expect(stats.player?.civilizations).toEqual([]);
  });
  it('serializes competing claims and interaction retries', async () => {
    const m = await fill(config());
    await act('civ_start', 0, { matchId: m.id });
    const r = await round(m.id),
      a = action('civ_claim', 0, { matchId: m.id, generation: r.generation, choice: 3 });
    const results = await Promise.all([
      stub().play(guild, a),
      act('civ_claim', 1, { matchId: m.id, generation: r.generation, choice: 3 }),
    ]);
    expect(results.filter((x) => x.ok === false)).toHaveLength(1);
    await stub().play(guild, a);
    expect(Object.keys((await round(m.id)).picks)).toHaveLength(1);
  });
  it('persists private picks across restart and times out using the shared alarm', async () => {
    const m = await fill(config());
    await act('civ_start', 0, { matchId: m.id, minutes: 1 });
    await act('civ_claim', 0, { matchId: m.id, generation: 1, choice: 1 });
    await abortAllDurableObjects();
    expect((await round(m.id)).picks[person(0).id]).toBe(1);
    await runInDurableObject(stub(), (_i, state) => {
      const sql = state.storage.sql,
        r = new CivStore(sql, () => {}).get(m.id)!;
      r.closesAt = Date.now() - 1;
      sql.exec('UPDATE civ_rounds SET config=? WHERE match_id=?', JSON.stringify(r), m.id);
      sql.exec("UPDATE deadlines SET due=0 WHERE kind='civ'");
    });
    await runDurableObjectAlarm(stub());
    const r = await round(m.id);
    expect(r.status).toBe('closed');
    expect(r.timedOut).toHaveLength(3);
    expect(r.picks[person(0).id]).toBe(1);
  });
  it('requires initiator/admin redo, rejects stale controls and invalidates a changed roster', async () => {
    const m = await fill(config());
    await act('civ_start', 0, { matchId: m.id });
    expect(await act('civ_start', 0, { matchId: m.id, redo: true })).toMatchObject({ ok: false });
    await runInDurableObject(stub(), (_i, state) => {
      const r = new CivStore(state.storage.sql, () => {}).get(m.id)!;
      r.openedAt -= 31000;
      state.storage.sql.exec(
        'UPDATE civ_rounds SET config=? WHERE match_id=?',
        JSON.stringify(r),
        m.id,
      );
    });
    expect(await act('civ_start', 1, { matchId: m.id, redo: true })).toMatchObject({ ok: false });
    await act('civ_start', 0, { matchId: m.id, redo: true });
    expect((await round(m.id)).generation).toBe(2);
    expect(await act('civ_claim', 0, { matchId: m.id, generation: 1, choice: 0 })).toMatchObject({
      ok: false,
    });
    await act('force_sub', 99, {
      admin: true,
      matchId: m.id,
      target: person(0),
      replacement: person(4),
    });
    expect((await round(m.id)).status).toBe('invalidated');
  });
  it('prefers unpicked choices from each catalog and uses the normal expired DLC classification', () => {
    const used = [...aoe2Civs.base.slice(0, 34), ...aoe2Civs.paid.slice(0, 10)].map(
      (selection) => ({ selection, at: Date.now() }),
    );
    const pool = selectionPool(aoe2Civs, used, () => 0.5);
    expect(pool.every((x) => !used.some((h) => h.selection === x))).toBe(true);
    expect(aoe2Civs.paid).toEqual(expect.arrayContaining(['Danes', 'Saxons', 'Varangians']));
  });
});
describe('identity evidence and actual civilizations', () => {
  const roster = (): IdentityPlayer[] =>
    Array.from({ length: 8 }, (_, i) => ({
      userId: `d${i}`,
      name: `UniqueName${i}`,
      side: i < 4 ? 0 : 1,
      profileId: i === 3 ? null : `p${i}`,
      provenance: 'player',
    }));
  const obs = (): LobbyObservation => ({
    externalId: '1',
    name: 'x',
    phase: 'started',
    observedAt: Date.now(),
    startedAt: Date.now() - 1,
    finishedAt: null,
    players: Array.from({ length: 8 }, (_, i) => ({
      profileId: `p${i}`,
      name: `Unrelated ${i}`,
      team: i < 4 ? '1' : '2',
      selection: 'Franks',
    })),
    capacity: 8,
    map: null,
    source: 'fixture',
  });
  it('infers the fourth teammate only from three independent anchors', () => {
    expect(inferLinks(roster(), obs())).toEqual([
      { userId: 'd3', profileId: 'p3', method: 'team', confidence: 0.95 },
    ]);
    const r = roster();
    r[0].provenance = 'inferred:name';
    expect(inferLinks(r, obs())).toEqual([]);
  });
  it('does not use count alone, absent anchors, wrong teams or extra guests', () => {
    for (const mutate of [
      (r: IdentityPlayer[], o: LobbyObservation) => {
        r[2].profileId = null;
      },
      (r: IdentityPlayer[], o: LobbyObservation) => {
        o.players[0].profileId = 'guest';
      },
      (r: IdentityPlayer[], o: LobbyObservation) => {
        o.players[0].team = '2';
      },
      (r: IdentityPlayer[], o: LobbyObservation) => {
        o.players.push({ profileId: 'guest', name: 'guest', team: '1' });
      },
    ]) {
      const r = roster(),
        o = obs();
      mutate(r, o);
      expect(inferLinks(r, o)).toEqual([]);
    }
  });
  it('matches unique normalized names and close long names, but rejects ambiguous or short names', () => {
    const r = roster().map((p) => ({ ...p, profileId: null })),
      o = obs();
    r[3].name = 'StarlightFox';
    o.players[3].name = 'Starlight Fox';
    expect(inferLinks(r, o)).toEqual([
      { userId: 'd3', profileId: 'p3', method: 'name', confidence: 1 },
    ]);
    o.players[2].name = o.players[3].name;
    expect(inferLinks(r, o)).toEqual([]);
    expect(nameSimilarity('ab', 'ab')).toBe(0);
    expect(nameSimilarity('TheGreatWarrior', 'TheGreatWarriorX')).toBeGreaterThan(0.9);
  });
  it('records only established links, never picker choices, and respects unlink rejection', async () => {
    let m = await fill(config());
    const o = observation(m);
    o.players.forEach((p) => (p.name = 'unmatchable'));
    await stub().recordAction(
      guild,
      {
        id: crypto.randomUUID(),
        kind: 'link',
        gameId: game,
        userId: person(0).id,
        provider: 'aoe2',
        externalId: '100',
        url: 'https://www.aoe2companion.com/profile/100',
      },
      person(0).id,
      false,
    );
    m = await attach(m);
    await act('lobby_confirm', 99, { admin: true, matchId: m.id, reference: m.lobby!.id });
    m = await current();
    await runInDurableObject(stub(), (_i, state) => {
      observeGamePlayers(state.storage.sql, m, 'aoe2', o);
      observeGamePlayers(state.storage.sql, m, 'aoe2', o);
    });
    const rows = await runInDurableObject(stub(), (_i, state) =>
      state.storage.sql.exec('SELECT * FROM played_selections').toArray(),
    );
    expect(rows).toHaveLength(1);
    expect(rows[0].external_id).toBe('100');
    await act('loss', Number(BigInt(m.captains[1]) - 100000000000000100n), { matchId: m.id });
    let stats = await stub().statistics(guild, game, person(0).id, 0, true, person(0).id);
    expect(stats.player?.civilizations[0].games).toBe(1);
    await stub().recordAction(
      guild,
      {
        id: crypto.randomUUID(),
        kind: 'unlink',
        gameId: game,
        userId: person(0).id,
        provider: 'aoe2',
        externalId: '100',
      },
      person(0).id,
      false,
    );
    o.players.find((p) => p.profileId === '100')!.name = person(0).name;
    await runInDurableObject(stub(), (_i, state) =>
      observeGamePlayers(state.storage.sql, m, 'aoe2', o),
    );
    stats = await stub().statistics(guild, game, person(0).id, 0, true, person(0).id);
    expect(stats.player?.profiles).toHaveLength(0);
    expect(stats.player?.civilizations).toEqual([]);
  });
  it('keeps name matches as suggestions until declared confirmation and excludes undone civ rates', async () => {
    let m = await fill(config());
    m = await attach(m);
    const o = observation(m, Date.now());
    expect(await accept(m, o)).toMatchObject({ accepted: true });
    let stats = await stub().statistics(guild, game, person(0).id, 0, true, person(0).id);
    expect(stats.player?.profiles).toEqual([]);
    expect(stats.player?.civilizations).toEqual([]);
    expect((await current()).lobby?.identitySuggestions).toHaveLength(4);
    await stub().recordAction(
      guild,
      {
        id: crypto.randomUUID(),
        kind: 'link',
        gameId: game,
        userId: person(0).id,
        provider: 'aoe2',
        externalId: '100',
        url: 'https://www.aoe2companion.com/profile/100',
      },
      person(0).id,
      false,
    );
    m = await attach(await current());
    await act('lobby_confirm', 99, { admin: true, matchId: m.id, reference: m.lobby!.id });
    m = await current();
    expect(await accept(m, observation(m, Date.now()))).toMatchObject({ accepted: true });
    await act('loss', Number(BigInt(m.captains[1]) - 100000000000000100n), { matchId: m.id });
    stats = await stub().statistics(guild, game, person(0).id, 0, true, person(0).id);
    expect(stats.player?.civilizations[0]).toMatchObject({ games: 1, inferred: 0 });
    await act('undo', 99, { admin: true, matchId: m.id, reason: 'fixture correction' });
    expect(
      (await stub().statistics(guild, game, person(0).id, 0, true, person(0).id)).player
        ?.civilizations,
    ).toEqual([]);
  });
});
describe('civilization attribution corrections', () => {
  it('does not credit an outgoing player with a replacement roster’s result', async () => {
    let m = await fill(config());
    m = await attach(m);
    await accept(m, observation(m, m.createdAt));
    expect(
      await act('force_sub', 99, {
        admin: true,
        matchId: m.id,
        target: person(0),
        replacement: person(4),
      }),
    ).not.toMatchObject({ ok: false });
    m = await current();
    await act('loss', Number(BigInt(m.captains[1]) - 100000000000000100n), { matchId: m.id });
    expect(
      (await stub().statistics(guild, game, person(0).id, 0, true, person(0).id)).player
        ?.civilizations,
    ).toEqual([]);
  });
  it('updates an existing observation’s identity and side when corrected evidence is captured', async () => {
    let m = await fill(config());
    await stub().recordAction(
      guild,
      {
        id: crypto.randomUUID(),
        kind: 'link',
        gameId: game,
        userId: person(0).id,
        provider: 'aoe2',
        externalId: '100',
        url: 'https://www.aoe2companion.com/profile/100',
      },
      person(0).id,
      false,
    );
    m = await attach(m);
    await act('lobby_confirm', 99, { admin: true, matchId: m.id, reference: m.lobby!.id });
    m = await current();
    const o = observation(m, m.createdAt);
    await runInDurableObject(stub(), (_i, state) => {
      const sql = state.storage.sql;
      observeGamePlayers(sql, m, 'aoe2', o);
      sql.exec(
        "UPDATE profile_refs SET external_id='999' WHERE game_id=? AND user_id=?",
        game,
        person(0).id,
      );
      const corrected = structuredClone(o);
      corrected.players.find((p) => p.profileId === '100')!.profileId = '999';
      observeGamePlayers(sql, m, 'aoe2', corrected);
      expect(
        sql
          .exec<{ external_id: string }>(
            'SELECT external_id FROM played_selections WHERE match_id=? AND user_id=?',
            m.id,
            person(0).id,
          )
          .toArray()[0].external_id,
      ).toBe('999');
    });
  });
});
describe('automatic betting cutoff', () => {
  async function market() {
    let m = await fill(config());
    await act('bet', 40, { matchId: m.id, side: 'alpha', stake: 50 });
    await act('bet', 41, { matchId: m.id, side: 'beta', stake: 50 });
    m = await attach(m);
    await act('lobby_confirm', 99, { admin: true, matchId: m.id, reference: m.lobby!.id });
    return current();
  }
  it('closes both-sided pools at positive start once and settles through the existing ledger', async () => {
    const m = await market();
    const cutoff = Date.now();
    await runInDurableObject(stub(), (_i, state) =>
      state.storage.sql.exec("UPDATE gold_ledger SET created_at=? WHERE kind='bet'", cutoff - 1000),
    );
    const o = observation(m, cutoff);
    expect(await accept(m, o)).toMatchObject({ accepted: true });
    await accept(m, o);
    let b = (await stub().records(guild)).economy.books[0];
    expect(b.status).toBe('frozen');
    expect(b.closed_at).toBe(cutoff);
    expect(await act('bet_cancel', 40, { matchId: m.id })).toMatchObject({ ok: false });
    await act('loss', Number(BigInt(m.captains[1]) - 100000000000000100n), { matchId: m.id });
    b = (await stub().records(guild)).economy.books[0];
    expect(b.status).toBe('settled');
    const imbalance = await runInDurableObject(stub(), (_i, state) =>
      state.storage.sql
        .exec(
          'SELECT w.user_id FROM wallets w JOIN gold_ledger l ON w.user_id=l.user_id AND w.game_id=l.game_id GROUP BY w.user_id HAVING w.balance!=SUM(l.amount)',
        )
        .toArray(),
    );
    expect(imbalance).toEqual([]);
  });
  it.each(['stake', 'cancel'])(
    'refunds a delayed start with late %s activity including already-cancelled stakes',
    async (kind) => {
      const m = await market(),
        cutoff = Date.now() - 100;
      if (kind === 'cancel')
        await runInDurableObject(stub(), (_i, state) => {
          const e = new EconomyStore(state.storage.sql),
            copy = structuredClone(m);
          delete copy.lobby;
          e.cancel(copy, person(40).id, 'late-cancel');
        });
      await accept(m, observation(m, cutoff));
      expect((await stub().records(guild)).economy.books[0]).toMatchObject({
        status: 'refunded',
        reason: 'late_start_evidence',
      });
      expect(
        await runInDurableObject(stub(), (_i, state) =>
          state.storage.sql.exec('SELECT * FROM bet_participation').toArray(),
        ),
      ).toEqual([]);
    },
  );
  it('pauses changes without fresh occupancy and refunds unresolved start evidence at report', async () => {
    const m = await market();
    expect(await act('bet', 42, { matchId: m.id, side: 'alpha', stake: 50 })).toMatchObject({
      ok: false,
    });
    await act('bet_close', 99, { admin: true, matchId: m.id });
    await act('loss', Number(BigInt(m.captains[1]) - 100000000000000100n), { matchId: m.id });
    expect((await stub().records(guild)).economy.books[0]).toMatchObject({
      status: 'refunded',
      reason: 'unverified_game_start',
    });
  });
  it('a full lobby is pre-game evidence, not a closure signal; stale occupancy pauses cancellations', async () => {
    const m = await market(),
      o = { ...observation(m), phase: 'full' as const, startedAt: null };
    await accept(m, o);
    expect((await stub().records(guild)).economy.books[0].status).toBe('open');
    expect((await act('bet', 42, { matchId: m.id, side: 'alpha', stake: 50 })).ok).not.toBe(false);
    await runInDurableObject(stub(), (_i, state) => {
      const copy = JSON.parse(
        state.storage.sql
          .exec<{ config: string }>('SELECT config FROM matches WHERE id=?', m.id)
          .toArray()[0].config,
      );
      copy.lobby.lastSuccessAt = Date.now() - 61000;
      state.storage.sql.exec('UPDATE matches SET config=? WHERE id=?', JSON.stringify(copy), m.id);
    });
    expect(await act('bet_cancel', 42, { matchId: m.id })).toMatchObject({ ok: false });
  });
});
describe('provider retry and bounded enrichment', () => {
  it('keeps occupancy fresh without violating an HTTP Retry-After delay', async () => {
    let m = await fill(config());
    m = await attach(m);
    const snapshot = await stub().read(guild),
      o = { ...observation(m), phase: 'open' as const, startedAt: null };
    vi.mocked(fetch).mockClear();
    vi.mocked(fetch).mockImplementation(
      async () => new Response(null, { status: 429, headers: { 'Retry-After': '3600' } }),
    );
    const poll = () =>
      runInDurableObject(stub(), async (_i, state) => {
        state.storage.sql.exec("UPDATE deadlines SET due=0 WHERE kind='lobby'");
        const store = new LobbyStore(state, () => snapshot.settings, {
          availability: () => 'available',
          match: (id) =>
            JSON.parse(
              state.storage.sql
                .exec<{ config: string }>('SELECT config FROM matches WHERE id=?', id)
                .toArray()[0].config,
            ),
          save: (m) =>
            state.storage.sql.exec(
              'UPDATE matches SET config=? WHERE id=?',
              JSON.stringify(m),
              m.id,
            ),
          prompt: () => {},
          audit: () => {},
          occupancy: async () => o,
        });
        await store.poll(guild);
      });
    await poll();
    expect((await current()).lobby?.observation?.phase).toBe('open');
    expect((await current()).lobby?.providerRetryAt).toBeGreaterThan(Date.now() + 3590000);
    await poll();
    expect(
      vi
        .mocked(fetch)
        .mock.calls.filter(([input]) => String(input).includes('data.aoe2companion.com')),
    ).toHaveLength(1);
  });
  it('continues civilization enrichment briefly after manual reporting, without reopening bets', async () => {
    let m = await fill(config());
    for (const p of m.roster)
      await stub().recordAction(
        guild,
        {
          id: crypto.randomUUID(),
          kind: 'link',
          gameId: game,
          userId: p.id,
          provider: 'aoe2',
          externalId: String(BigInt(p.id) - 100000000000000000n),
          url: `https://www.aoe2companion.com/profile/${BigInt(p.id) - 100000000000000000n}`,
        },
        p.id,
        false,
      );
    m = await attach(m);
    await act('loss', Number(BigInt(m.captains[1]) - 100000000000000100n), { matchId: m.id });
    m = await current();
    const o = {
      ...observation(m, m.createdAt),
      phase: 'finished' as const,
      finishedAt: Date.now(),
    };
    o.observedAt = o.finishedAt;
    expect(
      await stub().connectorEnded(guild, {
        schemaVersion: 1,
        communityId: guild,
        gameId: game,
        matchId: m.id,
        bindingId: m.lobby!.id,
        rosterVersion: m.rosterVersion ?? 0,
        adapterId: 'aoe2',
        eventId: crypto.randomUUID(),
        observation: o,
      }),
    ).toMatchObject({ accepted: true });
    expect(
      (await stub().statistics(guild, game, person(0).id, 0, true, person(0).id)).player
        ?.civilizations[0].games,
    ).toBe(1);
    expect((await stub().records(guild)).economy.books[0].status).toBe('refunded');
  });
});
describe('real lobby event shapes and freshness', () => {
  it('reduces snapshots, joins, changes and removals without interpreting absence as start', () => {
    const feed = new AoE2LobbySnapshot(),
      tracked = new Set(['9001']),
      now = Date.now();
    feed.consume(
      [
        {
          type: 'lobbyAdded',
          data: { matchId: 9001, name: 'Live lobby', totalSlotCount: 3, blockedSlotCount: 1 },
        },
        { type: 'slotAdded', data: { matchId: 9001, slot: 0, profileId: 100, name: 'A', team: 1 } },
        { type: 'slotAdded', data: { matchId: 9001, slot: 1, profileId: 101, name: 'B', team: 2 } },
      ],
      tracked,
      now,
    );
    expect(feed.read('9001', now)).toBeNull();
    expect(feed.read('9001', now + 2000)).toMatchObject({
      phase: 'full',
      capacity: 2,
      startedAt: null,
    });
    feed.consume({ type: 'slotRemoved', data: { matchId: 9001, slot: 1 } }, tracked, now + 3000);
    expect(feed.read('9001', now + 3000)?.players).toHaveLength(1);
    feed.consume(
      { type: 'slotUpdated', data: { matchId: 9001, slot: 0, team: 2 } },
      tracked,
      now + 4000,
    );
    expect(feed.read('9001', now + 4000)?.players[0].team).toBe('2');
    expect(feed.read('9001', now + 100000)).toBeNull();
    feed.consume({ type: 'lobbyRemoved', data: { matchId: 9001 } }, tracked, now + 5000);
    expect(feed.read('9001', now + 5000)).toBeNull();
  });
  it('requires a complete new baseline on reconnect and never calls unknown capacity full', () => {
    const f = new AoE2LobbySnapshot(),
      ids = new Set(['1']),
      now = Date.now();
    f.consume({ type: 'lobbyAdded', data: { matchId: 1, name: 'Unknown' } }, ids, now);
    expect(f.read('1', now + 2000)?.phase).toBe('open');
    f.clear();
    f.consume({ type: 'slotAdded', data: { matchId: 1, slot: 1, profileId: 2 } }, ids, now + 3000);
    expect(f.read('1', now + 6000)).toBeNull();
  });
});

describe('shared occupancy recovery', () => {
  it('retains active subscriptions across a process restart and prunes expired leases', async () => {
    const feed = () => env.AOE2_LOBBIES.getByName('recovery-fixture');
    await feed().query('9001');
    await abortAllDurableObjects();
    expect(await feed().status()).toMatchObject({ healthy: false, tracked: 1 });
    await runInDurableObject(feed(), async (_i, state) => {
      await state.storage.put('leases', [['9001', Date.now() - 1]]);
    });
    await abortAllDurableObjects();
    await runDurableObjectAlarm(feed());
    expect(await feed().status()).toMatchObject({ healthy: false, tracked: 0 });
  });
});

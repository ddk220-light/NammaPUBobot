import { runInDurableObject, runDurableObjectAlarm, abortAllDurableObjects } from 'cloudflare:test';
import { describe, it, expect } from 'vitest';
import { harness, stub, settings, fill, current, act, person, guild, game } from './harness';
import {
  createMapRound,
  defaultMaps,
  resolveMap,
  mapSettingsSchema,
  aoe2Maps,
} from '../src/domain/maps';
import { matchCard } from '../src/discord/cards';
import { settingsSchema } from '../src/domain/config';
harness();
const config = (mode: 'direct' | 'random' | 'vote' = 'vote', check = true) => {
  const s = settings();
  s.queues[0].modules.maps = true;
  s.queues[0].maps = { ...defaultMaps(['Arabia', 'Arena', 'Nomad', 'Black Forest']), mode };
  s.queues[0].checkIn.enabled = check;
  return s;
};
describe('M8 optional map selection', () => {
  it('stays off by default and preloads AoE2 only when enabled', async () => {
    const s = settings();
    s.games[0].catalogId = 'aoe2';
    const m = await fill(s);
    expect(m.mapRound).toBeUndefined();
  });
  it('uses the AoE2 starter pool without claiming an external connector', async () => {
    const s = settings();
    s.games[0].catalogId = 'aoe2';
    s.queues[0].modules.maps = true;
    const m = await fill(s);
    expect(m.mapRound!.choices.every((v) => aoe2Maps.includes(v))).toBe(true);
  });
  it('requires a custom-game pool and validates names/counts/check-in mode', async () => {
    const s = settings();
    await stub().save(guild, 0, s, person(99).id, 'save');
    s.queues[0].modules.maps = true;
    expect(settingsSchema.safeParse(s).success).toBe(false);
    // Old preference-only saves remain protected at formation while the operator repairs the pool.
    await runInDurableObject(stub(), (_i, state) =>
      state.storage.sql.exec(
        'UPDATE queues SET config=? WHERE id=?',
        JSON.stringify(s.queues[0]),
        s.queues[0].id,
      ),
    );
    for (let n = 0; n < 3; n++) await act('add', n);
    expect((await act('add', 3)).ok).toBe(false);
    expect((await stub().records(guild)).waiting[0].players).toHaveLength(3);
    expect(
      mapSettingsSchema.safeParse({ ...defaultMaps(), pool: ['Arena', 'arena'] }).success,
    ).toBe(false);
    expect(
      mapSettingsSchema.safeParse({ ...defaultMaps(), mode: 'direct', countsAsCheckIn: true })
        .success,
    ).toBe(false);
  });
  it('map voting can replace check-in and reaches play only after all confirmations', async () => {
    const s = config();
    s.queues[0].maps!.countsAsCheckIn = true;
    const m = await fill(s),
      r = m.mapRound!;
    expect((await act('ready', 0, { matchId: m.id })).ok).toBe(false);
    for (let n = 0; n < 4; n++) {
      await act('map_vote', n, { matchId: m.id, roundId: r.id, mapChoices: [r.choices[0]] });
      if (n < 3) expect((await current()).state).toBe('check_in');
    }
    const next = await current();
    expect(next.state).toBe('playing');
    expect(next.ready).toHaveLength(4);
    expect(next.mapRound!.selected).toEqual([r.choices[0]]);
    await runInDurableObject(stub(), (_i, s) =>
      expect(s.storage.sql.exec('SELECT * FROM readiness').toArray()).toHaveLength(4),
    );
  });
  it('all standard check-ins wait for maps; random maps do not add another preparation stage', async () => {
    const m = await fill(config());
    for (let n = 0; n < 4; n++) await act('ready', n, { matchId: m.id });
    expect((await current()).state).toBe('check_in');
    for (let n = 0; n < 4; n++)
      await act('map_vote', n, {
        matchId: m.id,
        roundId: m.mapRound!.id,
        mapChoices: [m.mapRound!.choices[0]],
      });
    expect((await current()).state).toBe('playing');
  });
  it('votes work with check-in off and spectators cannot vote', async () => {
    const m = await fill(config('vote', false)),
      r = m.mapRound!;
    expect(
      (await act('map_vote', 9, { matchId: m.id, roundId: r.id, mapChoices: [r.choices[0]] })).ok,
    ).toBe(false);
    for (let n = 0; n < 4; n++)
      await act('map_vote', n, { matchId: m.id, roundId: r.id, mapChoices: [r.choices[0]] });
    expect((await current()).state).toBe('playing');
  });
  it('approval supports several maps, changes ballots, and deduplicates selections', async () => {
    const s = config('vote', false);
    s.queues[0].maps!.voting = 'approval';
    s.queues[0].maps!.count = 2;
    const m = await fill(s),
      r = m.mapRound!;
    await act('map_vote', 0, {
      matchId: m.id,
      roundId: r.id,
      mapChoices: [r.choices[0], r.choices[1]],
    });
    await act('map_vote', 0, {
      matchId: m.id,
      roundId: r.id,
      mapChoices: [r.choices[1], r.choices[2]],
    });
    for (let n = 1; n < 4; n++)
      await act('map_vote', n, {
        matchId: m.id,
        roundId: r.id,
        mapChoices: [r.choices[1], r.choices[2]],
      });
    expect((await current()).mapRound!.selected).toEqual([r.choices[1], r.choices[2]]);
  });
  it('a captain direct pick needs exactly the configured count', async () => {
    const s = config('direct', false);
    s.queues[0].maps!.count = 2;
    const m = await fill(s),
      r = m.mapRound!,
      n = Number(BigInt(m.captains[0]) - 100000000000000100n);
    expect(
      (
        await act('map_pick', 9, {
          matchId: m.id,
          roundId: r.id,
          mapChoices: r.choices.slice(0, 2),
        })
      ).ok,
    ).toBe(false);
    expect(
      (
        await act('map_pick', n, {
          matchId: m.id,
          roundId: r.id,
          mapChoices: r.choices.slice(0, 1),
        })
      ).ok,
    ).toBe(false);
    await act('map_pick', n, { matchId: m.id, roundId: r.id, mapChoices: r.choices.slice(0, 2) });
    expect((await current()).state).toBe('playing');
  });
  it('no-vote deadline persists across restart and resolves one saved random choice', async () => {
    const m = await fill(config('vote', false));
    await abortAllDurableObjects();
    await runInDurableObject(stub(), (_i, s) => {
      s.storage.sql.exec("UPDATE deadlines SET due=0 WHERE kind='map'");
    });
    await runDurableObjectAlarm(stub());
    const result = (await current()).mapRound!.selected;
    expect(result).toHaveLength(1);
    await runDurableObjectAlarm(stub());
    expect((await current()).mapRound!.selected).toEqual(result);
  });
  it('no-vote cancellation releases restrictions and removes all voting controls', async () => {
    const s = config('vote', false);
    s.queues[0].maps!.noVotes = 'cancel';
    const m = await fill(s);
    await runInDurableObject(stub(), (_i, s) =>
      s.storage.sql.exec("UPDATE deadlines SET due=0 WHERE kind='map'"),
    );
    await runDurableObjectAlarm(stub());
    expect((await current()).state).toBe('aborted');
    expect(matchCard(await current()).components).toEqual([]);
    expect((await act('add', 0)).ok).not.toBe(false);
  });
  it('redo invalidates the old round, stays within absolute lifetime, and refunds a closed book', async () => {
    const s = config('vote', false);
    s.queues[0].modules.gold = true;
    s.queues[0].modules.predictions = true;
    const m = await fill(s),
      r = m.mapRound!;
    await act('bet', 8, { matchId: m.id, side: 'alpha', stake: 50 });
    await act('bet', 9, { matchId: m.id, side: 'beta', stake: 50 });
    await act('bet_close', 99, { matchId: m.id, admin: true });
    await act('map_redo', 99, { matchId: m.id, admin: true });
    const next = await current();
    expect(next.mapRound!.id).not.toBe(r.id);
    expect(next.expiresAt).toBe(m.expiresAt);
    expect(
      (await act('map_vote', 0, { matchId: m.id, roundId: r.id, mapChoices: [r.choices[0]] })).ok,
    ).toBe(false);
    expect((await stub().records(guild)).economy.books[0].status).toBe('refunded');
  });
  it('saved tie order does not reroll on retry and cooldown resets only when needed', () => {
    const rules = { ...defaultMaps(['A', 'B']), count: 2, shortlist: 2 };
    const r = createMapRound(rules, ['A', 'B'], 0);
    expect(r.note).toContain('reset');
    resolveMap(r);
    const selected = r.selected.slice();
    resolveMap(r);
    expect(r.selected).toEqual(selected);
  });
});

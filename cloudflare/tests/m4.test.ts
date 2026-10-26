import { env, exports } from 'cloudflare:workers';
import {
  reset,
  runInDurableObject,
  runDurableObjectAlarm,
  abortAllDurableObjects,
} from 'cloudflare:test';
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { emptyModules, type Settings } from '../src/domain/config';
import { defaultRecords, type ImportBundle, type RecordAction } from '../src/domain/records';
import type { Player } from '../src/domain/rating';
import { seedCsv, csvRows } from '../src/domain/import-csv';

const guild = '706320010368516198',
  channel = '1559279290577850368',
  game = '4daebc0a-55e8-4d56-a17d-d94913644910',
  queue = '81da571b-6a4a-4e5d-8e32-f4050df35a2d';
const uid = (n: number) => String(100000000000000100n + BigInt(n)),
  person = (n: number) => ({ id: uid(n), name: `Player ${n}` });
const cfg: Settings = {
  games: [
    {
      id: game,
      name: 'RTS',
      catalogId: null,
      records: { ...defaultRecords, minimumMatches: 0, recentDays: 0 },
    },
  ],
  queues: [
    {
      id: queue,
      name: 'Pickup',
      gameId: game,
      channelId: channel,
      size: 2,
      enabled: true,
      checkIn: { enabled: false, seconds: 60 },
      teamMethod: 'captain',
      ratingEngine: 'aoe2',
      modules: emptyModules,
    },
  ],
};
const stub = (g = guild) => env.COMMUNITIES.getByName(g);
const p = (n: number): Player => ({
  ...person(n),
  rating: 1000,
  deviation: 200,
  wins: 0,
  losses: 0,
  draws: 0,
  streak: 0,
});
const bundle = (players = [p(0), p(1)]): ImportBundle => ({
  version: 1,
  source: 'csv',
  sourceId: 'sample',
  mode: 'seed',
  mappings: [{ ladder: 'default', gameId: game, queues: { default: queue } }],
  players: players.map((player) => ({ ladder: 'default', player })),
  matches: [],
  changes: [],
  unsupported: [],
});
async function setup(c = cfg, g = guild) {
  expect(await stub(g).save(g, 0, c, uid(99), 'save')).toMatchObject({ ok: true });
}
async function apply(b = bundle()) {
  const preview = await stub().importPreview(guild, b);
  expect(preview.ok).toBe(true);
  const result = await stub().importApply(guild, b, preview.target!, uid(99));
  if ('error' in result) throw new Error(result.error);
  return result;
}
const act = (kind: RecordAction['kind'], n = 0, extra: Partial<RecordAction> = {}, admin = true) =>
  stub().recordAction(
    guild,
    { id: crypto.randomUUID(), kind, userId: uid(n), gameId: game, ...extra },
    admin ? uid(99) : uid(n),
    admin,
  );
async function manual(result: 'alpha' | 'beta' | 'draw' = 'alpha') {
  return stub().play(guild, {
    id: crypto.randomUUID(),
    kind: 'manual_create',
    actor: person(99),
    admin: true,
    channelId: channel,
    queue,
    result,
    teams: [[person(0)], [person(1)]],
  });
}
async function sql<T>(callback: (s: SqlStorage) => T) {
  return runInDurableObject(stub(), (_i, state) => callback(state.storage.sql));
}
let permissions = '32';
beforeEach(() => {
  permissions = '32';
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const req = new Request(input, init),
      url = new URL(req.url);
    if (url.pathname.endsWith('/users/@me/guilds'))
      return Response.json([{ id: guild, name: 'Friends', permissions }]);
    if (url.pathname.endsWith('/roles'))
      return Response.json([77, 78].map((n) => ({ id: uid(n), managed: false, permissions: '0' })));
    if (url.pathname.endsWith('/channels')) return Response.json([]);
    if (/\/members\/\d+$/.test(url.pathname))
      return Response.json({ roles: [], user: { username: 'friend' } });
    return Response.json({ id: uid(90) });
  });
});
afterEach(async () => {
  await reset();
  vi.restoreAllMocks();
});
describe('guarded onboarding', () => {
  it('inserts seeds once and retains existing ratings when a later seed disagrees', async () => {
    await setup();
    await apply();
    expect((await apply()).alreadyApplied).toBe(true);
    const next = bundle([{ ...p(0), rating: 1800 }, p(2)]);
    next.sourceId = 'next';
    expect((await apply(next)).counts).toMatchObject({ newPlayers: 1, existingPlayers: 1 });
    expect((await stub().statistics(guild, game, uid(0))).player?.rating).toBe(1000);
    expect((await stub().statistics(guild, game)).total).toBe(3);
  });
  it('rejects stale previews after a played game without writing any new player', async () => {
    await setup();
    await apply();
    const b = bundle([p(2)]),
      preview = await stub().importPreview(guild, b);
    await manual();
    await expect(stub().importApply(guild, b, preview.target!, uid(99))).resolves.toMatchObject({
      ok: false,
      error: expect.stringContaining('changed after preview'),
    });
    expect((await stub().statistics(guild, game, uid(2))).player).toBeNull();
  });
  it('allows a privileged manual adjustment but never treats it as a bulk seed overwrite', async () => {
    await setup();
    await apply();
    await stub().play(guild, {
      id: crypto.randomUUID(),
      kind: 'seed',
      admin: true,
      actor: person(99),
      channelId: channel,
      queue,
      target: person(0),
      rating: 1234,
    });
    expect((await stub().statistics(guild, game, uid(0))).player?.rating).toBe(1234);
  });
  it('rejects duplicate players and source IDs before writes', async () => {
    await setup();
    const b = bundle([p(0), p(0)]);
    expect((await stub().importPreview(guild, b)).errors.join()).toContain('Duplicate player');
    expect((await stub().statistics(guild, game)).total).toBe(0);
  });
  it('rejects source ladders merged into one destination and source queues outside the chosen game', async () => {
    await setup();
    const b = bundle();
    b.mappings.push({ ...b.mappings[0], ladder: 'other' });
    expect((await stub().importPreview(guild, b)).errors.join()).toContain('cannot be merged');
    b.mappings[1].gameId = crypto.randomUUID();
    expect((await stub().importPreview(guild, b)).errors.join()).toContain('Queue mapping');
  });
  it('keeps community and game player identities independent', async () => {
    const c = structuredClone(cfg),
      second = crypto.randomUUID();
    c.games.push({ id: second, name: 'Another RTS', catalogId: null });
    c.queues.push({ ...c.queues[0], id: crypto.randomUUID(), name: 'Another', gameId: second });
    await setup(c);
    await setup(cfg, '706320010368516199');
    await apply();
    await manual();
    expect((await stub().statistics(guild, second, uid(0))).player).toBeNull();
    expect(
      (await stub('706320010368516199').statistics('706320010368516199', game, uid(0))).player,
    ).toBeNull();
  });
  it('parses quoted CSV and rejects malformed, ambiguous timestamps and duplicate shapes', () => {
    expect(csvRows('id,name\r\n123,"A, B"\r\n')[1]).toEqual(['123', 'A, B']);
    expect(() => csvRows('"open')).toThrow('unclosed');
    expect(() =>
      seedCsv(
        'id,name,rating,deviation,lastRankedAt\n100000000000000100,Test,1000,200,2026-10-01',
        game,
        queue,
        'sample',
      ),
    ).toThrow('timezone');
    expect(() =>
      seedCsv('id,name,rating,deviation\n100000000000000100,Test,1000', game, queue, 'sample'),
    ).toThrow('columns');
  });
});
function history(): ImportBundle {
  const b = bundle([
    { ...p(0), rating: 1048, wins: 1, streak: 1 },
    { ...p(1), rating: 952, losses: 1, streak: -1 },
  ]);
  b.source = 'nammapubobot';
  b.mode = 'history';
  b.mappings[0].engine = 'aoe2';
  const at = Date.now() - 40 * 86400000;
  b.matches = [
    {
      sourceId: 'old-1',
      ladder: 'default',
      queue: 'default',
      at,
      endedAt: at,
      result: 'alpha',
      teams: [[uid(0)], [uid(1)]],
    },
  ];
  b.changes = [0, 1].map((n) => ({
    sourceId: String(n),
    ladder: 'default',
    userId: uid(n),
    matchId: 'old-1',
    at,
    before: 1000,
    after: n ? 952 : 1048,
    deviationBefore: 200,
    deviationAfter: 200,
    reason: 'Original ranked result',
  }));
  return b;
}
describe('historical reconciliation and corrections', () => {
  for (const engine of ['flat', 'glicko2', 'trueskill', 'aoe2'] as const)
    for (const result of ['alpha', 'beta', 'draw'] as const)
      it(`${engine}: ${result} is durable and can be undone`, async () => {
        const c = structuredClone(cfg);
        c.queues[0].ratingEngine = engine;
        await setup(c);
        await apply();
        expect((await manual(result)).ok).not.toBe(false);
        await abortAllDurableObjects();
        const m = (await stub().records(guild)).matches[0];
        expect(m.state).toBe('finished');
        const before = (await stub().statistics(guild, game, uid(0))).player!;
        expect(before.matchCount).toBe(1);
        await stub().play(guild, {
          id: crypto.randomUUID(),
          kind: 'undo',
          actor: person(99),
          admin: true,
          channelId: channel,
          matchId: m.id,
          version: m.version,
        });
        expect((await stub().statistics(guild, game, uid(0))).player).toMatchObject({
          rating: 1000,
          deviation: 200,
          wins: 0,
          losses: 0,
          draws: 0,
        });
      });
  it('reconciles historical counters, source IDs, timestamps, ratings and idempotent manifests across restart', async () => {
    await setup();
    const b = history();
    await apply(b);
    await abortAllDurableObjects();
    const stats = await stub().statistics(guild, game, uid(0));
    expect(stats.player).toMatchObject({
      wins: 1,
      rating: 1048,
      lastRankedAt: b.matches[0].endedAt,
      source: 'nammapubobot:sample',
    });
    expect(stats.player?.history[0]).toMatchObject({
      before: 1000,
      after: 1048,
      reason: 'Original ranked result',
      deviation_before: 200,
    });
    expect(stats.player?.recent).toHaveLength(1);
    expect((await apply(b)).alreadyApplied).toBe(true);
    expect((await stub().records(guild)).delivery).toHaveLength(0);
  });
  it('reverses imported original deltas without inventing a new historical calculation', async () => {
    await setup();
    await apply(history());
    const m = (await stub().records(guild)).matches[0];
    const reply = await stub().play(guild, {
      id: crypto.randomUUID(),
      kind: 'undo',
      admin: true,
      actor: person(99),
      channelId: channel,
      matchId: m.id,
      version: m.version,
    });
    expect(reply.ok).not.toBe(false);
    expect((await stub().statistics(guild, game, uid(0))).player).toMatchObject({
      rating: 1000,
      wins: 0,
      streak: 0,
      lastRankedAt: null,
    });
  });
  it('rejects a historical streak that disagrees with the original results', async () => {
    await setup();
    const b = history();
    b.players[0].player.streak = 0;
    expect((await stub().importPreview(guild, b)).errors.join()).toContain(
      'streak does not reconcile',
    );
    expect((await stub().records(guild)).players).toHaveLength(0);
  });
  it('removes outgoing substitutes from match statistics and last-played dates', async () => {
    await setup();
    await apply(bundle([p(0), p(1), p(2)]));
    for (const n of [0, 1])
      await stub().play(guild, {
        id: crypto.randomUUID(),
        kind: 'add',
        admin: false,
        actor: person(n),
        channelId: channel,
        queue,
      });
    const m = (await stub().records(guild)).matches[0];
    const sub = await stub().play(guild, {
      id: crypto.randomUUID(),
      kind: 'force_sub',
      actor: person(99),
      admin: true,
      channelId: channel,
      matchId: m.id,
      target: person(0),
      replacement: person(2),
    });
    expect(sub.ok).not.toBe(false);
    const updated = (await stub().records(guild)).matches[0];
    await stub().play(guild, {
      id: crypto.randomUUID(),
      kind: 'admin_report',
      actor: person(99),
      admin: true,
      channelId: channel,
      matchId: m.id,
      version: updated.version,
      result: 'alpha',
    });
    const outgoing = (await stub().statistics(guild, game, uid(0))).player!;
    expect(outgoing.recent).toHaveLength(0);
    expect(outgoing.lastRankedAt).toBeNull();
    expect((await stub().statistics(guild, game, uid(2))).player?.recent).toHaveLength(1);
  });
  it('rejects missing deltas, unmatched models, mismatched totals and final history gaps', async () => {
    await setup();
    const b = history();
    b.players[0].player.wins = 2;
    b.changes[0].after = 1100;
    b.mappings[0].engine = 'glicko2';
    b.changes.pop();
    const errors = (await stub().importPreview(guild, b)).errors.join();
    expect(errors).toContain('totals');
    expect(errors).toContain('Final rating');
    expect(errors).toContain('source rating model');
    expect(errors).toContain('exactly one');
  });
  it('requires an empty destination for full history, preserving already-played games', async () => {
    await setup();
    await apply();
    expect((await stub().importPreview(guild, history())).errors.join()).toContain(
      'empty destination',
    );
  });
  it('rolls back the entire import when a storage failure interrupts history insertion', async () => {
    await setup();
    await sql((s) =>
      s
        .exec(
          "CREATE TRIGGER interrupt_import BEFORE INSERT ON rating_changes BEGIN SELECT RAISE(ABORT,'Injected interruption'); END",
        )
        .toArray(),
    );
    const b = history(),
      preview = await stub().importPreview(guild, b);
    await expect(stub().importApply(guild, b, preview.target!, uid(99))).resolves.toMatchObject({
      ok: false,
      status: 500,
    });
    expect((await stub().records(guild)).players).toHaveLength(0);
    expect((await stub().records(guild)).matches).toHaveLength(0);
    expect((await stub().records(guild)).onboarding.imports).toHaveLength(0);
    await sql((s) => s.exec('DROP TRIGGER interrupt_import').toArray());
    await apply(b);
  });
});
describe('profile identity and visibility', () => {
  it('links multiple optional references, handles conflicts explicitly and records privileged reassignment', async () => {
    await setup();
    await apply();
    const ref = {
      provider: 'steam',
      externalId: '123',
      url: 'https://steamcommunity.com/profiles/123',
    };
    await act('link', 0, ref, false);
    await expect(act('link', 1, ref, false)).resolves.toMatchObject({
      ok: false,
      content: expect.stringContaining('already linked'),
    });
    await expect(act('reassign', 1, ref)).resolves.toMatchObject({
      ok: false,
      content: expect.stringContaining('reason'),
    });
    await act('reassign', 1, { ...ref, reason: 'Owner confirmed' });
    expect((await stub().statistics(guild, game, uid(0))).player?.profiles).toHaveLength(0);
    expect(
      (await stub().records(guild)).onboarding.audit.some(
        (r) => r.kind === 'reassign' && JSON.parse(String(r.details)).previousOwner === uid(0),
      ),
    ).toBe(true);
    expect((await stub().statistics(guild, game, uid(1))).player?.profiles[0].provenance).toBe(
      'admin',
    );
    await act('link', 1, { provider: 'aoe2', externalId: '42', url: 'https://aoe2.net/42' }, false);
    expect((await stub().statistics(guild, game, uid(1))).player?.profiles).toHaveLength(2);
    await act('unlink', 1, ref, false);
    expect((await stub().statistics(guild, game, uid(1))).player?.profiles).toHaveLength(1);
  });
  it('hides players consistently from boards, direct lookup and other player match histories', async () => {
    await setup();
    await apply();
    await manual();
    await act('hide', 1);
    expect((await stub().statistics(guild, game)).total).toBe(1);
    expect((await stub().statistics(guild, game, uid(1))).player).toBeNull();
    const own = await stub().statistics(guild, game, uid(0));
    expect(JSON.stringify(own)).not.toContain(uid(1));
    expect(own.player?.recent[0].teams[1][0]).toEqual({ id: null, name: 'Hidden player' });
    expect((await stub().statistics(guild, game, uid(1), 0, false, uid(1))).player?.hidden).toBe(
      true,
    );
    await act('unhide', 1);
    expect((await stub().statistics(guild, game)).total).toBe(2);
  });
  it('requires admin authority for hiding and prevents cross-player profile changes', async () => {
    await setup();
    await apply();
    await expect(act('hide', 0, {}, false)).resolves.toMatchObject({
      ok: false,
      content: expect.stringContaining('organizers'),
    });
    await expect(
      stub().recordAction(
        guild,
        {
          id: crypto.randomUUID(),
          kind: 'link',
          gameId: game,
          userId: uid(1),
          provider: 'steam',
          externalId: '1',
          url: 'https://example.com',
        },
        uid(0),
        false,
      ),
    ).resolves.toMatchObject({ ok: false, content: expect.stringContaining('organizers') });
  });
  it('counts teammate and opponent samples from ranked results and excludes undone results', async () => {
    await setup();
    await apply(bundle([p(0), p(1), p(2), p(3)]));
    for (const result of ['alpha', 'beta', 'alpha'] as const)
      await stub().play(guild, {
        id: crypto.randomUUID(),
        kind: 'manual_create',
        actor: person(99),
        admin: true,
        channelId: channel,
        queue,
        result,
        teams: [
          [person(0), person(2)],
          [person(1), person(3)],
        ],
      });
    const stats = (await stub().statistics(guild, game, uid(0))).player!;
    expect(stats.relationships.find((r) => r.userId === uid(2))).toMatchObject({
      teammateMatches: 3,
      winsTogether: 2,
      opponentMatches: 0,
    });
    expect(stats.relationships.find((r) => r.userId === uid(1))).toMatchObject({
      teammateMatches: 0,
      opponentMatches: 3,
      winsAgainst: 2,
    });
    const m = (await stub().records(guild)).matches[0];
    await stub().play(guild, {
      id: crypto.randomUUID(),
      kind: 'undo',
      actor: person(99),
      admin: true,
      channelId: channel,
      matchId: m.id,
      version: m.version,
    });
    expect((await stub().statistics(guild, game, uid(0))).player?.relationships).toHaveLength(0);
  });
  it('filters old imported players by recency while keeping their individual history available', async () => {
    const c = structuredClone(cfg);
    c.games[0].records!.recentDays = 30;
    await setup(c);
    await apply(history());
    const stats = await stub().statistics(guild, game, uid(0));
    expect(stats.total).toBe(0);
    expect(stats.player?.eligible).toBe(false);
    expect(stats.player?.history).toHaveLength(1);
    c.games[0].records!.recentDays = 0;
    await stub().save(guild, 1, c, uid(99), 'save');
    expect((await stub().statistics(guild, game)).total).toBe(2);
  });
  it('paginates an explicit zero-match policy and defaults to eligibility after one match', async () => {
    await setup();
    await apply(bundle(Array.from({ length: 12 }, (_, n) => p(n))));
    expect((await stub().statistics(guild, game, undefined, 0)).leaderboard).toHaveLength(10);
    expect((await stub().statistics(guild, game, undefined, 1)).leaderboard).toHaveLength(2);
    const c = structuredClone(cfg);
    delete c.games[0].records;
    await stub().save(guild, 1, c, uid(99), 'save');
    expect((await stub().statistics(guild, game)).total).toBe(0);
    await manual();
    expect((await stub().statistics(guild, game)).total).toBe(2);
  });
});
describe('weekly decay and Discord rank delivery', () => {
  it('does not decay old profiles while their new match is active', async () => {
    const c = structuredClone(cfg);
    c.games[0].records!.inactivity.enabled = true;
    await setup(c);
    await apply(history());
    for (const n of [0, 1])
      await stub().play(guild, {
        id: crypto.randomUUID(),
        kind: 'add',
        admin: false,
        actor: person(n),
        channelId: channel,
        queue,
      });
    expect((await stub().records(guild)).matches.some((m) => m.state === 'playing')).toBe(true);
    await sql((s) => s.exec('UPDATE record_clocks SET due=?', Date.now() - 1).toArray());
    await runDurableObjectAlarm(stub());
    expect((await stub().statistics(guild, game, uid(0))).player?.rating).toBe(1048);
    expect((await stub().statistics(guild, game, uid(1))).player?.rating).toBe(952);
  });
  it('decays after the 30-day grace, respects rank floors and bounds deviations, without changing WLD or streak', async () => {
    const c = structuredClone(cfg);
    c.games[0].records!.inactivity.enabled = true;
    await setup(c);
    await apply(history());
    await sql((s) => s.exec('UPDATE record_clocks SET due=?', Date.now() - 1).toArray());
    await runDurableObjectAlarm(stub());
    const p = (await stub().statistics(guild, game, uid(0))).player!;
    expect(p).toMatchObject({ rating: 1038, deviation: 200, wins: 1, streak: 1 });
    const second = (await stub().statistics(guild, game, uid(1))).player!;
    expect(second.rating).toBe(942);
    await runDurableObjectAlarm(stub());
    expect((await stub().statistics(guild, game, uid(0))).player?.rating).toBe(1038);
  });
  it('skips never-played and recently played players and removes the weekly clock when disabled', async () => {
    const c = structuredClone(cfg);
    c.games[0].records!.inactivity.enabled = true;
    await setup(c);
    await apply();
    await manual();
    await sql((s) => s.exec('UPDATE record_clocks SET due=?', Date.now() - 1).toArray());
    await runDurableObjectAlarm(stub());
    expect((await stub().statistics(guild, game, uid(0))).player?.rating).toBe(1048);
    c.games[0].records!.inactivity.enabled = false;
    await stub().save(guild, 1, c, uid(99), 'save');
    expect((await stub().records(guild)).onboarding.clocks).toHaveLength(0);
  });
  it('removes inactive-game alarms when its last queue is removed', async () => {
    const c = structuredClone(cfg);
    c.games[0].records!.inactivity.enabled = true;
    await setup(c);
    expect((await stub().records(guild)).onboarding.clocks).toHaveLength(1);
    c.queues = [];
    await stub().save(guild, 1, c, uid(99), 'save');
    expect((await stub().records(guild)).onboarding.clocks).toHaveLength(0);
  });
  it('blocks a saved rank role that acquires privileged permissions before delivery', async () => {
    const c = structuredClone(cfg);
    c.games[0].records!.ranks = [{ threshold: 0, label: 'F', roleId: uid(77) }];
    await setup(c);
    await apply();
    for (const bit of [3, 13, 22, 34]) {
      vi.mocked(fetch).mockImplementation(async (input, init) =>
        new URL(new Request(input, init).url).pathname.endsWith('/roles')
          ? Response.json([
              { id: uid(77), permissions: (1n << BigInt(bit)).toString(), managed: false },
            ])
          : Response.json({ roles: [], user: { username: 'friend' } }),
      );
      await act('sync');
      await stub().flush();
      expect((await stub().records(guild)).onboarding.ranks[0]).toMatchObject({
        status: 'blocked',
        error: expect.stringContaining('privileged'),
      });
    }
    expect(
      vi
        .mocked(fetch)
        .mock.calls.some(([input, init]) =>
          ['PUT', 'DELETE', 'PATCH'].includes(new Request(input, init).method),
        ),
    ).toBe(false);
    expect((await stub().statistics(guild, game, uid(0))).player?.rating).toBe(1000);
  });
  it('blocks cosmetic rank roles with privileged channel overrides before any member mutation', async () => {
    const c = structuredClone(cfg);
    c.games[0].records!.ranks = [{ threshold: 0, label: 'F', roleId: uid(77) }];
    c.games[0].records!.decorateNickname = true;
    await setup(c);
    await apply();
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const path = new URL(new Request(input, init).url).pathname;
      if (path.endsWith('/roles'))
        return Response.json([{ id: uid(77), permissions: '0', managed: false }]);
      if (path.endsWith('/channels'))
        return Response.json([
          { permission_overwrites: [{ id: uid(77), type: 0, allow: '8192' }] },
        ]);
      return Response.json({ roles: [], user: { username: 'friend' } });
    });
    await stub().flush();
    expect((await stub().records(guild)).onboarding.ranks[0]).toMatchObject({
      status: 'blocked',
      error: expect.stringContaining('privileged channel override'),
    });
    const calls = vi.mocked(fetch).mock.calls.map(([input, init]) => new Request(input, init));
    expect(calls.some((r) => ['PUT', 'DELETE', 'PATCH'].includes(r.method))).toBe(false);
    expect(calls.filter((r) => new URL(r.url).pathname.endsWith('/channels'))).toHaveLength(1);
    expect((await stub().statistics(guild, game, uid(0))).player?.rating).toBe(1000);
  });
  it('retains a result when role delivery is denied and allows an explicit retry', async () => {
    const c = structuredClone(cfg);
    c.games[0].records!.ranks = [{ threshold: 0, label: 'F', roleId: uid(77) }];
    await setup(c);
    await apply();
    vi.mocked(fetch).mockResolvedValue(new Response('{}', { status: 403 }));
    await manual();
    await stub().flush();
    expect((await stub().statistics(guild, game, uid(0))).player?.rating).toBe(1048);
    expect((await stub().records(guild)).onboarding.ranks[0]).toMatchObject({
      status: 'blocked',
      error: 'Discord HTTP 403',
    });
    vi.mocked(fetch).mockImplementation(async (input, init) =>
      new URL(new Request(input, init).url).pathname.endsWith('/roles')
        ? Response.json([77, 78].map((n) => ({ id: uid(n), managed: false, permissions: '0' })))
        : new URL(new Request(input, init).url).pathname.endsWith('/channels')
          ? Response.json([])
          : /\/members\/\d+$/.test(new URL(new Request(input, init).url).pathname)
            ? Response.json({ roles: [], user: { username: 'friend' } })
            : new Response(null, { status: 204 }),
    );
    await act('sync');
    await stub().flush();
    expect(
      (await stub().records(guild)).onboarding.ranks.find((r) => r.user_id === uid(0))?.status,
    ).toBe('sent');
  });
  it('adds desired ranks, removes only configured old ranks and decorates nicknames', async () => {
    const c = structuredClone(cfg);
    c.games[0].records!.ranks = [
      { threshold: 0, label: 'F', roleId: uid(77) },
      { threshold: 1000, label: 'C', roleId: uid(78) },
    ];
    c.games[0].records!.decorateNickname = true;
    await setup(c);
    await apply();
    vi.mocked(fetch).mockImplementation(async (input, init) =>
      new URL(new Request(input, init).url).pathname.endsWith('/roles')
        ? Response.json([77, 78].map((n) => ({ id: uid(n), managed: false, permissions: '0' })))
        : new URL(new Request(input, init).url).pathname.endsWith('/channels')
          ? Response.json([])
          : /\/members\/\d+$/.test(new URL(new Request(input, init).url).pathname) &&
              new Request(input, init).method === 'GET'
            ? Response.json({
                roles: [uid(77), uid(79)],
                nick: '[900] Friend',
                user: { username: 'friend' },
              })
            : new Response(null, { status: 204 }),
    );
    await stub().flush();
    const calls = vi.mocked(fetch).mock.calls.map(([input, init]) => new Request(input, init));
    expect(calls.some((r) => r.method === 'DELETE' && r.url.endsWith(`/roles/${uid(77)}`))).toBe(
      true,
    );
    expect(calls.some((r) => r.method === 'DELETE' && r.url.endsWith(`/roles/${uid(79)}`))).toBe(
      false,
    );
    expect(calls.some((r) => r.method === 'PUT' && r.url.endsWith(`/roles/${uid(78)}`))).toBe(true);
    expect(await calls.find((r) => r.method === 'PATCH')!.json()).toEqual({
      nick: '[1000] Friend',
    });
  });
});
describe('HTTP authorization', () => {
  const session = 'a'.repeat(64),
    csrf = 'b'.repeat(64),
    base = 'https://setup.example';
  const headers = {
    Cookie: `__Host-cg-session=${session}`,
    Origin: base,
    'Content-Type': 'application/json',
    'X-CSRF-Token': csrf,
  };
  async function login() {
    await env.AUTH.getByName(`session:${session}`).saveSession({
      user: { id: uid(99), username: 'admin', avatar: null },
      accessToken: 'test-access',
      expiresAt: Date.now() + 60000,
      csrf,
    });
  }
  it('requires membership for member statistics, permits configured public reads and rechecks revoked admin permissions', async () => {
    await setup();
    await apply();
    expect((await exports.default.fetch(`${base}/statistics/${guild}/${game}`)).status).toBe(403);
    await login();
    permissions = '0';
    expect(
      (await exports.default.fetch(`${base}/statistics/${guild}/${game}`, { headers })).status,
    ).toBe(200);
    expect(
      (
        await exports.default.fetch(`${base}/api/communities/${guild}/import/preview`, {
          method: 'POST',
          headers,
          body: JSON.stringify(bundle()),
        })
      ).status,
    ).toBe(403);
    const c = structuredClone(cfg);
    c.games[0].records!.visibility = 'public';
    await stub().save(guild, 1, c, uid(99), 'save');
    const publicStats = await exports.default.fetch(`${base}/statistics/${guild}/${game}`);
    expect(publicStats.status).toBe(200);
    expect(publicStats.headers.get('cache-control')).toBe('no-store');
    c.games[0].records!.visibility = 'admins';
    await stub().save(guild, 2, c, uid(99), 'save');
    expect(
      (await exports.default.fetch(`${base}/statistics/${guild}/${game}`, { headers })).status,
    ).toBe(403);
  });
  it('rejects mutations without CSRF and returns bounded validation errors', async () => {
    await setup();
    await login();
    expect(
      (
        await exports.default.fetch(`${base}/api/communities/${guild}/import/apply`, {
          method: 'POST',
          headers: { ...headers, 'X-CSRF-Token': 'bad' },
          body: '{}',
        })
      ).status,
    ).toBe(403);
    expect(
      (
        await exports.default.fetch(`${base}/api/communities/${guild}/player`, {
          method: 'POST',
          headers,
          body: '{}',
        })
      ).status,
    ).toBe(400);
  });
});

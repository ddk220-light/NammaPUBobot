import { env, exports } from 'cloudflare:workers';
import {
  abortAllDurableObjects,
  reset,
  runDurableObjectAlarm,
  runInDurableObject,
} from 'cloudflare:test';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { emptyModules, settingsSchema, type Settings } from '../src/domain/config';
import { balance, type Action, type Match } from '../src/domain/play';
import { defaultRatingSettings, rateTeams, type Player } from '../src/domain/rating';
import { commands } from '../src/discord/commands.mjs';
import { interactions } from '../src/discord/interactions';
import fixtures from './fixtures/legacy-m2.json';

const guild = '706320010368516198',
  otherGuild = '706320010368516199',
  channel = '706320010368516200';
const game = '4daebc0a-55e8-4d56-a17d-d94913644910',
  queue = '81da571b-6a4a-4e5d-8e32-f4050df35a2d';
const role = '100000000000000999';
const person = (n: number, roles: string[] = []) => ({
  id: String(100000000000000100n + BigInt(n)),
  name: `Player ${n}`,
  roles,
});
let seq = 100000000000005000n;
let memberRoles: string[] = [];
const config: Settings = {
  games: [{ id: game, name: 'Custom RTS', catalogId: null }],
  queues: [
    {
      id: queue,
      gameId: game,
      channelId: channel,
      name: 'Pickup',
      size: 4,
      enabled: true,
      checkIn: { enabled: true, seconds: 60 },
      teamMethod: 'captain',
      ratingEngine: 'aoe2',
      modules: emptyModules,
    },
  ],
};
const stub = (g = guild) => env.COMMUNITIES.getByName(g);
const records = () => stub().records(guild);
const action = (kind: Action['kind'], n = 0, extra: Partial<Action> = {}): Action => ({
  id: String(seq++),
  actor: person(n),
  channelId: channel,
  admin: false,
  kind,
  ...extra,
});
const act = (kind: Action['kind'], n = 0, extra: Partial<Action> = {}, g = guild) =>
  stub(g).play(g, action(kind, n, extra));
async function setup(c = config) {
  expect(await stub().save(guild, 0, c, person(99).id, 'save')).toMatchObject({ ok: true });
}
async function fill(checking = true, method: Settings['queues'][0]['teamMethod'] = 'captain') {
  const c = structuredClone(config);
  c.queues[0].checkIn.enabled = checking;
  c.queues[0].teamMethod = method;
  await setup(c);
  for (let n = 0; n < 4; n++) await act('add', n);
  return (await records()).matches[0];
}
async function due(kind = 'check_in', id?: string) {
  await runInDurableObject(stub(), (_i, s) => {
    const matches = s.storage.sql
      .exec<{ config: string }>("SELECT config FROM matches WHERE state IN ('check_in','playing')")
      .toArray();
    for (const row of matches) {
      const m = JSON.parse(row.config) as Match;
      if (id && m.id !== id) continue;
      if (kind === 'check_in') {
        m.deadline = Date.now() - 1000;
        s.storage.sql.exec('UPDATE matches SET config=? WHERE id=?', JSON.stringify(m), m.id);
      }
      s.storage.sql.exec(
        'UPDATE deadlines SET due=? WHERE match_id=? AND kind=?',
        Date.now() - 1000,
        m.id,
        kind,
      );
    }
  });
  await runDurableObjectAlarm(stub());
}
async function saved(id: string) {
  return (await records()).matches.find((m) => m.id === id)!;
}
beforeEach(() => {
  seq = 100000000000005000n;
  memberRoles = [];
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const req = new Request(input, init),
      url = new URL(req.url);
    if (url.pathname.endsWith('/users/@me/guilds'))
      return Response.json([{ id: guild, name: 'Friends', permissions: '32' }]);
    const match = /\/guilds\/\d+\/members\/(\d+)$/.exec(url.pathname);
    if (match)
      return Response.json({
        roles: memberRoles,
        user: { id: match[1], username: `member-${match[1]}` },
      });
    return Response.json({ id: '100000000000008888' });
  });
});
afterEach(async () => {
  await reset();
  vi.restoreAllMocks();
});

describe('team modes and bounded commands', () => {
  it('an unknown match in automatic substitution returns a useful rejection without throwing', async () => {
    await setup();
    const reply = await act('subauto', 0, { matchId: crypto.randomUUID() });
    expect(reply.ok).toBe(false);
    expect((await records()).matches).toEqual([]);
  });
  it('matches independent legacy unrestricted allocations', () => {
    for (const f of fixtures.unrestricted)
      expect(balance(f.players, 'balanced').map((t) => t.map((p) => p.id))).toEqual(f.expected);
  });
  it('random allocations retain every player exactly once and highest-rated captains', async () => {
    const players = Array.from({ length: 16 }, (_, n) => ({
      ...person(n),
      rating: 500 + n * 100,
      deviation: 200,
      wins: 0,
      losses: 0,
      draws: 0,
      streak: 0,
    }));
    for (let n = 0; n < 20; n++) {
      const teams = balance(players, 'random');
      expect(teams.map((t) => t.length)).toEqual([8, 8]);
      expect(new Set(teams.flat().map((p) => p.id)).size).toBe(16);
      expect(
        teams
          .flat()
          .map((p) => p.id)
          .sort(),
      ).toEqual(players.map((p) => p.id).sort());
      expect(teams.every((t) => t[0].rating === Math.max(...t.map((p) => p.rating)))).toBe(true);
    }
    const m = await fill(false, 'random');
    expect(m.state).toBe('playing');
    expect(m.queue.teamMethod).toBe('random');
  });
  it('validates aliases and recursively checks Discord option constraints and required ordering', () => {
    const c = structuredClone(config);
    c.queues[0].aliases = ['Pickup'];
    expect(settingsSchema.safeParse(c).success).toBe(false);
    type CommandOption = {
      name: string;
      description: string;
      type: number;
      required?: boolean;
      options?: CommandOption[];
    };
    const check = (options: CommandOption[] | undefined) => {
      let optional = false;
      expect(options?.length ?? 0).toBeLessThanOrEqual(25);
      for (const o of options ?? []) {
        expect(o.name.length).toBeLessThanOrEqual(32);
        expect(o.description.length).toBeLessThanOrEqual(100);
        if (o.type !== 1) {
          if ('required' in o && o.required) expect(optional).toBe(false);
          else optional = true;
        }
        if ('options' in o) check(o.options);
      }
    };
    commands.forEach((c) => check(c.options));
  });
});

describe('substitutions and roster concurrency', () => {
  it('subauto retains readiness, removes a queued substitute and refreshes balanced teams/captains', async () => {
    const m = await fill();
    await act('ready', 1, { matchId: m.id });
    await act('ready', 2, { matchId: m.id });
    await act('add', 4);
    await act('seed', 99, { admin: true, target: person(4), rating: 1900 });
    expect((await act('subauto', 0, { matchId: m.id, version: m.version })).content).toContain(
      'rebalanced',
    );
    const updated = await saved(m.id);
    expect(updated.roster.map((p) => p.id)).not.toContain(person(0).id);
    expect(updated.ready.sort()).toEqual([person(1).id, person(2).id]);
    expect(updated.teams.map((t) => t.map((p) => p.id))).toEqual(
      balance(updated.roster, 'balanced').map((t) => t.map((p) => p.id)),
    );
    expect(updated.captains).toEqual(updated.teams.map((t) => t[0].id));
    expect(updated.deadline).toBeGreaterThan(Date.now() + 58000);
    expect((await records()).waiting[0].players).toHaveLength(0);
    expect((await act('ready', 0, { matchId: m.id, version: m.version })).content).toContain(
      'changed',
    );
    expect((await act('add', 0)).content).toContain('Joined');
  });
  it('two competing subauto calls cannot take the same queued player', async () => {
    const m = await fill(false);
    await act('add', 4);
    const replies = await Promise.all([
      act('subauto', 0, { matchId: m.id, version: m.version }),
      act('subauto', 1, { matchId: m.id, version: m.version }),
    ]);
    expect(replies.filter((r) => r.ok !== false)).toHaveLength(1);
    const updated = await saved(m.id);
    expect(updated.roster.filter((p) => p.id === person(4).id)).toHaveLength(1);
    expect(updated.roster).toHaveLength(4);
  });
  it('requests toggle; subfor needs a request; duplicate replacement delivery is idempotent', async () => {
    const m = await fill(false);
    expect((await act('subfor', 4, { target: person(0), matchId: m.id })).ok).toBe(false);
    await act('subme', 0, { matchId: m.id });
    await act('subme', 0, { matchId: m.id });
    expect((await saved(m.id)).subRequests).toEqual([]);
    await act('subme', 0, { matchId: m.id });
    await act('add', 4);
    const a = action('subfor', 4, { target: person(0), matchId: m.id });
    expect(await stub().play(guild, a)).toEqual(await stub().play(guild, a));
    const updated = await saved(m.id);
    expect(updated.roster.filter((p) => p.id === person(4).id)).toHaveLength(1);
    expect(updated.subRequests).toEqual([]);
    expect(updated.teams.findIndex((t) => t.some((p) => p.id === person(4).id))).toBe(
      m.teams.findIndex((t) => t.some((p) => p.id === person(0).id)),
    );
  });
  it('explicit substitute request actions keep their desired state across repeated panel clicks', async () => {
    const m = await fill(false);
    const request = { matchId: m.id, version: m.version, subRequest: true };
    expect((await act('subme', 0, request)).content).toContain('Substitute requested');
    expect((await act('subme', 0, request)).content).toContain('already looking');
    expect((await saved(m.id)).subRequests).toEqual([person(0).id]);

    const cancel = { ...request, subRequest: false };
    expect((await act('subme', 0, cancel)).content).toContain('request cancelled');
    expect((await act('subme', 0, cancel)).content).toContain('already cancelled');
    expect((await saved(m.id)).subRequests).toEqual([]);
    await runInDurableObject(stub(), (_instance, state) => {
      expect(
        state.storage.sql.exec("SELECT * FROM control_audit WHERE action='sub_request'").toArray(),
      ).toHaveLength(2);
    });
  });
  it('denies outsider forced replacements and unavailable/banned candidates', async () => {
    const m = await fill(false);
    await act('add', 4);
    await act('add', 5);
    expect((await act('subauto', 9, { target: person(0), matchId: m.id })).ok).toBe(false);
    await act('noadd', 99, { admin: true, target: person(4), seconds: 60 });
    await act('subauto', 0, { matchId: m.id });
    expect((await saved(m.id)).roster.map((p) => p.id)).toContain(person(5).id);
    expect(
      (
        await act('force_sub', 99, {
          admin: true,
          matchId: m.id,
          target: person(1),
          replacement: person(2),
        })
      ).ok,
    ).toBe(false);
  });
  it('forced replacement and put keep two nonempty teams and roll back invalid edits', async () => {
    const m = await fill(false);
    await act('add', 4);
    expect(
      (
        await act('force_sub', 99, {
          admin: true,
          matchId: m.id,
          target: person(0),
          replacement: person(4),
        })
      ).ok,
    ).not.toBe(false);
    expect(
      (await act('put', 99, { admin: true, matchId: m.id, target: person(5), side: 'alpha' })).ok,
    ).not.toBe(false);
    const updated = await saved(m.id);
    expect(updated.roster).toHaveLength(5);
    expect(
      updated.teams
        .flat()
        .map((p) => p.id)
        .sort(),
    ).toEqual(updated.roster.map((p) => p.id).sort());
    expect(
      (await act('put', 99, { admin: true, matchId: m.id, target: person(6), side: undefined })).ok,
    ).toBe(false);
    expect((await saved(m.id)).roster).toEqual(updated.roster);
  });
  it('backing out during check-in returns other ready players when no substitute exists', async () => {
    const m = await fill();
    await act('ready', 0, { matchId: m.id });
    await act('ready', 1, { matchId: m.id });
    await act('back', 0, { matchId: m.id });
    expect((await saved(m.id)).state).toBe('check_in_failed');
    expect((await records()).waiting[0].players.map((p) => p.id)).toEqual([person(1).id]);
  });
});

describe('no-shows, queue expiry and match timeouts', () => {
  it('replaces all missing players, retains readiness, and resumes the fresh window after restart', async () => {
    const m = await fill();
    await act('ready', 0, { matchId: m.id });
    await act('ready', 1, { matchId: m.id });
    await act('add', 4);
    await act('add', 5);
    await due();
    const updated = await saved(m.id);
    expect(updated.state).toBe('check_in');
    expect(updated.replacementRounds).toBe(1);
    expect(updated.ready).toEqual([person(0).id, person(1).id]);
    expect(updated.roster.map((p) => p.id).sort()).toEqual([0, 1, 4, 5].map((n) => person(n).id));
    await abortAllDurableObjects();
    expect((await saved(m.id)).deadline).toBe(updated.deadline);
    await act('ready', 4, { matchId: m.id, version: updated.version });
    await act('ready', 5, { matchId: m.id, version: updated.version });
    expect((await saved(m.id)).state).toBe('playing');
  });
  it('insufficient replacements fail safely, returning ready players ahead of waiting players', async () => {
    const m = await fill();
    await act('ready', 0, { matchId: m.id });
    await act('add', 4);
    await due();
    expect((await saved(m.id)).state).toBe('check_in_failed');
    expect((await records()).waiting[0].players.map((p) => p.id)).toEqual([
      person(0).id,
      person(4).id,
    ]);
  });
  it('caps repeated no-show rounds and does not keep an idle retry alarm alive', async () => {
    const m = await fill();
    await act('ready', 0, { matchId: m.id });
    for (let n = 4; n < 7; n++) await act('add', n);
    await runInDurableObject(stub(), (_i, s) => {
      const row = s.storage.sql
        .exec<{ config: string }>('SELECT config FROM matches WHERE id=?', m.id)
        .toArray()[0];
      const record = JSON.parse(row.config);
      record.replacementRounds = 3;
      s.storage.sql.exec('UPDATE matches SET config=? WHERE id=?', JSON.stringify(record), m.id);
    });
    await due();
    expect((await saved(m.id)).state).toBe('check_in_failed');
    await runInDurableObject(stub(), (_i, s) => {
      s.storage.sql.exec("UPDATE outbox SET due=0 WHERE status='pending'");
    });
    await stub().flush();
    await runInDurableObject(stub(), async (_i, s) =>
      expect(await s.storage.getAlarm()).toBeNull(),
    );
  });
  it('refreshes waiting expiry on a repeated add and expires queued players after restart', async () => {
    const c = structuredClone(config);
    c.queues[0].controls = {
      waitingSeconds: 60,
      matchSeconds: 0,
      requiredRole: null,
      blockedRole: null,
    };
    await setup(c);
    await act('add', 0);
    let first = 0;
    await runInDurableObject(stub(), (_i, s) => {
      first = s.storage.sql.exec<{ due: number }>('SELECT due FROM queue_clocks').toArray()[0].due;
      s.storage.sql.exec('UPDATE queue_clocks SET due=due-1000');
    });
    await act('add', 0);
    await runInDurableObject(stub(), (_i, s) => {
      expect(
        s.storage.sql.exec<{ due: number }>('SELECT due FROM queue_clocks').toArray()[0].due,
      ).toBeGreaterThanOrEqual(first);
      s.storage.sql.exec('UPDATE queue_clocks SET due=0');
    });
    await abortAllDurableObjects();
    await runDurableObjectAlarm(stub());
    expect((await records()).waiting[0].players).toEqual([]);
  });
  it('match timeout aborts without rating changes and releases all players', async () => {
    const c = structuredClone(config);
    c.queues[0].controls = {
      waitingSeconds: 0,
      matchSeconds: 60,
      requiredRole: null,
      blockedRole: null,
    };
    c.queues[0].checkIn.enabled = false;
    await setup(c);
    for (let n = 0; n < 4; n++) await act('add', n);
    const m = (await records()).matches[0];
    await abortAllDurableObjects();
    await due('lifetime', m.id);
    expect((await saved(m.id)).state).toBe('aborted');
    expect((await records()).changes).toEqual([]);
    expect((await records()).waiting[0].players).toHaveLength(4);
    expect((await act('add', 0)).content).toContain('match found');
  });
});

describe('queue controls, aliases and restrictions', () => {
  it('splits rating-sorted groups into independent matches with leftovers and normal check-in rules', async () => {
    const c = structuredClone(config);
    c.queues[0].size = 8;
    c.queues[0].autoStart = false;
    c.queues[0].checkIn.enabled = false;
    await setup(c);
    for (let n = 0; n < 7; n++) {
      await act('seed', 99, { admin: true, target: person(n), rating: 1000 + n * 100 });
      await act('add', n);
    }
    expect((await act('split', 99, { admin: true, groupSize: 3 })).ok).toBe(false);
    expect((await records()).matches).toHaveLength(0);
    await act('split', 99, { admin: true, groupSize: 2, sortByRating: true });
    const r = await records();
    expect(r.matches).toHaveLength(3);
    expect(r.matches.every((m) => m.roster.length === 2 && m.state === 'playing')).toBe(true);
    expect(r.matches.map((m) => m.roster.map((p) => p.id).sort()).sort()).toEqual(
      [
        [6, 5],
        [4, 3],
        [2, 1],
      ]
        .map((t) => t.map((n) => person(n).id).sort())
        .sort(),
    );
    expect(r.waiting[0].players.map((p) => p.id)).toEqual([person(0).id]);
  });
  it('clear is privileged and does not disturb active matches', async () => {
    const m = await fill(false);
    await act('add', 4);
    expect((await act('clear', 4)).ok).toBe(false);
    await act('clear', 99, { admin: true });
    expect((await records()).waiting[0].players).toEqual([]);
    expect((await saved(m.id)).state).toBe('playing');
  });
  it('aliases and default queue resolve without silently choosing among ambiguous queues', async () => {
    const c = structuredClone(config);
    c.queues[0].aliases = ['tg'];
    c.queues[0].isDefault = true;
    c.queues.push({
      ...c.queues[0],
      id: crypto.randomUUID(),
      name: 'Other',
      aliases: [],
      isDefault: false,
    });
    await setup(c);
    expect((await act('add', 0, { queue: 'TG' })).content).toContain('Pickup');
    expect((await act('add', 1)).content).toContain('Pickup');
  });
  it('enforces signed role evidence on join and fresh membership evidence on selection', async () => {
    const c = structuredClone(config);
    c.queues[0].autoStart = false;
    c.queues[0].controls = {
      waitingSeconds: 0,
      matchSeconds: 0,
      requiredRole: role,
      blockedRole: null,
    };
    await setup(c);
    expect((await act('add', 0)).ok).toBe(false);
    for (let n = 0; n < 2; n++)
      expect((await act('add', n, { actor: person(n, [role]) })).ok).not.toBe(false);
    memberRoles = [];
    expect((await act('start', 99, { admin: true })).ok).toBe(false);
    expect((await records()).matches).toEqual([]);
    memberRoles = [role];
    expect((await act('start', 99, { admin: true })).ok).not.toBe(false);
  });
  it('no-add is community-wide, removes waiting entries, expires lazily, and forgive permits rejoining', async () => {
    await setup();
    await stub(otherGuild).save(otherGuild, 0, config, person(99).id, 'save');
    await act('add', 0);
    expect((await act('noadd', 0, { target: person(1), seconds: 60 })).ok).toBe(false);
    await act('noadd', 99, { admin: true, target: person(0), seconds: 60, reason: 'test' });
    expect((await records()).waiting[0].players).toEqual([]);
    expect((await act('add', 0)).ok).toBe(false);
    expect((await act('add', 0, {}, otherGuild)).ok).not.toBe(false);
    await act('forgive', 99, { admin: true, target: person(0) });
    expect((await act('add', 0)).ok).not.toBe(false);
    await act('noadd', 99, { admin: true, target: person(0), seconds: 1 });
    await runInDurableObject(stub(), (_i, s) => s.storage.sql.exec('UPDATE noadds SET until_ms=0'));
    expect((await act('add', 0)).ok).not.toBe(false);
  });
});

async function manual(result: Action['result'] = 'alpha', extra: Partial<Action> = {}) {
  const reply = await act('manual_create', 99, {
    admin: true,
    teams: [[person(0)], [person(1)]],
    result,
    ...extra,
  });
  const id = /[a-f0-9]{8}-[a-f0-9-]{27}/.exec(reply.content)?.[0];
  expect(id).toBeTruthy();
  return saved(id!);
}
describe('manual results, undo and re-report audit', () => {
  it('manual create deduplicates its action and rejects duplicates or unprivileged creation', async () => {
    await setup();
    const a = action('manual_create', 99, {
      admin: true,
      teams: [[person(0)], [person(1)]],
      result: 'alpha',
    });
    expect(await stub().play(guild, a)).toEqual(await stub().play(guild, a));
    expect((await records()).matches).toHaveLength(1);
    expect(
      (await act('manual_create', 0, { teams: [[person(2)], [person(3)]], result: 'alpha' })).ok,
    ).toBe(false);
    expect(
      (
        await act('manual_create', 99, {
          admin: true,
          teams: [[person(2)], [person(2)]],
          result: 'alpha',
        })
      ).ok,
    ).toBe(false);
    expect((await records()).matches).toHaveLength(1);
  });
  it('undoing an older result subtracts only its deltas and recomputes counts/streaks from remaining results', async () => {
    await setup();
    const first = await manual('alpha');
    const second = await manual('beta');
    const before = (await records()).players;
    const a = action('undo', 99, { admin: true, matchId: first.id, version: first.version });
    await stub().play(guild, a);
    await stub().play(guild, a);
    const after = (await records()).players;
    for (const p of after) {
      const old = before.find((x) => x.id === p.id)!;
      const delta = first.changes.find((x) => x.userId === p.id)!;
      expect(p.rating).toBe(Math.max(0, old.rating - (delta.after - delta.before)));
      expect(p.wins + p.losses + p.draws).toBe(1);
    }
    expect(after.find((p) => p.id === person(0).id)?.streak).toBe(-1);
    expect(after.find((p) => p.id === person(1).id)?.streak).toBe(1);
    expect((await saved(second.id)).changes).toEqual(second.changes);
    expect((await saved(first.id)).state).toBe('undone');
    expect((await records()).audit.filter((a) => a.action === 'undo')).toHaveLength(1);
  });
  it('re-reports an undone result using current ratings once, with separate rating events', async () => {
    await setup();
    const m = await manual();
    await act('undo', 99, { admin: true, matchId: m.id });
    const before = (await records()).players;
    const teams = m.teams.map((t) =>
      t.map((p) => before.find((b) => b.id === p.id)!),
    ) as unknown as [Player[], Player[]];
    const expected = rateTeams(teams, 1, defaultRatingSettings).flat();
    await act('admin_report', 99, { admin: true, matchId: m.id, result: 'beta' });
    const updated = await saved(m.id);
    expect(updated.generation).toBe(1);
    expect(updated.result).toBe('beta');
    expect((await records()).players.map((p) => p.rating).sort()).toEqual(
      expected.map((p) => p.rating).sort(),
    );
    const repeat = await act('admin_report', 99, { admin: true, matchId: m.id, result: 'alpha' });
    expect(repeat.ok).toBe(false);
    expect((await records()).changes).toHaveLength(6);
  });
  it('undo preserves a later administrator deviation adjustment while reversing only the original change', async () => {
    await setup();
    const m = await manual();
    const current = (await records()).players.find((p) => p.id === person(0).id)!;
    expect(
      (
        await act('seed', 99, {
          admin: true,
          target: person(0),
          rating: current.rating,
          deviation: 700,
        })
      ).ok,
    ).not.toBe(false);
    await act('undo', 99, { admin: true, matchId: m.id });
    const original = m.roster.find((p) => p.id === person(0).id)!;
    const rated = m.ratedPlayers!.find((p) => p.id === original.id)!;
    expect((await records()).players.find((p) => p.id === original.id)?.deviation).toBe(
      700 - (rated.deviation - original.deviation),
    );
  });
  it('refuses corrections during affected active matches and keeps unrelated play independent', async () => {
    await setup();
    const m = await manual();
    for (let n = 0; n < 4; n++) await act('add', n);
    expect((await act('undo', 99, { admin: true, matchId: m.id })).ok).toBe(false);
    expect((await saved(m.id)).state).toBe('finished');
  });
  it('administrator reporting can resolve check-in while ordinary captains cannot bypass it', async () => {
    const m = await fill();
    expect((await act('loss', 0, { matchId: m.id })).ok).toBe(false);
    await act('admin_report', 99, { admin: true, matchId: m.id, result: 'draw' });
    expect((await saved(m.id)).result).toBe('draw');
    expect((await records()).players.every((p) => p.draws === 1)).toBe(true);
  });
  it('overlapping reports apply one rating event per participant', async () => {
    const m = await fill(false);
    const captain = Number(BigInt(m.captains[0]) - 100000000000000100n);
    await Promise.all([
      act('loss', captain, { matchId: m.id }),
      act('admin_report', 99, { admin: true, matchId: m.id, result: 'beta' }),
    ]);
    expect((await records()).changes).toHaveLength(4);
    expect((await saved(m.id)).state).toBe('finished');
  });
});

describe('delivery and web repairs', () => {
  it('recreates a deleted card from saved state without repeating ratings', async () => {
    const m = await fill(false);
    await stub().flush();
    await act('recover', 99, { admin: true, matchId: m.id });
    vi.mocked(fetch).mockImplementationOnce(async () => Response.json({}, { status: 404 }));
    await stub().flush();
    await runInDurableObject(stub(), (_i, s) => s.storage.sql.exec('UPDATE outbox SET due=0'));
    await stub().flush();
    expect(vi.mocked(fetch).mock.calls.some(([, init]) => init?.method === 'POST')).toBe(true);
    expect((await saved(m.id)).version).toBe(m.version);
    expect((await records()).changes).toHaveLength(0);
    expect((await records()).delivery).toEqual([]);
  });
  it('a substitution stays committed across transport failure and restart; retrying cannot substitute twice', async () => {
    const m = await fill(false);
    await stub().flush();
    await act('add', 4);
    const a = action('subauto', 0, { matchId: m.id });
    await stub().play(guild, a);
    vi.mocked(fetch).mockImplementationOnce(async () => {
      throw new Error('timeout');
    });
    await stub().flush();
    await abortAllDurableObjects();
    await stub().play(guild, a);
    await runInDurableObject(stub(), (_i, s) => s.storage.sql.exec('UPDATE outbox SET due=0'));
    await stub().flush();
    expect((await saved(m.id)).roster.map((p) => p.id)).toContain(person(4).id);
    expect((await records()).audit.filter((a) => a.action === 'roster_changed')).toHaveLength(1);
  });
  it('web repair enforces current authority, CSRF, input boundaries, and optimistic match versions', async () => {
    await setup();
    const m = await manual();
    const sid = 'a'.repeat(64),
      csrf = 'b'.repeat(64);
    await env.AUTH.getByName(`session:${sid}`).saveSession({
      user: { id: person(99).id, username: 'organizer', avatar: null },
      csrf,
      accessToken: 'test-access',
      expiresAt: Date.now() + 60000,
    });
    const headers = {
      Cookie: `__Host-cg-session=${sid}`,
      Origin: 'https://setup.example',
      'X-CSRF-Token': csrf,
      'Content-Type': 'application/json',
    };
    const body = {
      id: crypto.randomUUID(),
      kind: 'undo',
      channelId: channel,
      matchId: m.id,
      version: m.version,
    };
    const request = (value = body, h: HeadersInit = headers) =>
      exports.default.fetch(`https://setup.example/api/communities/${guild}/actions`, {
        method: 'POST',
        headers: h,
        body: JSON.stringify(value),
      });
    expect((await request(body, {})).status).toBe(401);
    expect((await request(body, { ...headers, 'X-CSRF-Token': 'bad' })).status).toBe(403);
    expect((await request({ ...body, admin: true } as typeof body)).status).toBe(400);
    expect((await request()).status).toBe(200);
    expect((await request({ ...body, id: crypto.randomUUID() })).status).toBe(409);
    vi.mocked(fetch).mockImplementation(async () =>
      Response.json([{ id: guild, permissions: '0' }]),
    );
    expect((await request()).status).toBe(403);
  });
  it('manual Discord record defers promptly while resolving actual server membership', async () => {
    await setup();
    const payload = {
      id: String(seq++),
      type: 2,
      application_id: '100000000000000001',
      guild_id: guild,
      channel_id: channel,
      token: 'test-interaction-token',
      member: { permissions: '32', roles: [], user: { ...person(99), username: 'organizer' } },
      data: {
        name: 'match',
        options: [
          {
            name: 'create',
            options: [
              { name: 'alpha', value: person(0).id },
              { name: 'beta', value: person(1).id },
              { name: 'result', value: 'alpha' },
            ],
          },
        ],
      },
    };
    const raw = JSON.stringify(payload),
      timestamp = String(Math.floor(Date.now() / 1000));
    const key = await crypto.subtle.importKey(
      'pkcs8',
      Uint8Array.from(
        '302e020100300506032b6570042204209d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60'
          .match(/../g)!
          .map((s) => parseInt(s, 16)),
      ),
      'Ed25519',
      false,
      ['sign'],
    );
    const sig = new Uint8Array(
      await crypto.subtle.sign('Ed25519', key, new TextEncoder().encode(timestamp + raw)),
    );
    const jobs: Promise<unknown>[] = [];
    const response = await interactions(
      new Request('https://setup.example/interactions', {
        method: 'POST',
        headers: {
          'X-Signature-Timestamp': timestamp,
          'X-Signature-Ed25519': Array.from(sig, (b) => b.toString(16).padStart(2, '0')).join(''),
        },
        body: raw,
      }),
      env,
      {
        waitUntil(p) {
          jobs.push(p);
        },
      },
    );
    expect(await response.json()).toEqual({ type: 5, data: { flags: 64 } });
    // A deferred interaction schedules card delivery after its record commits.
    // Drain jobs added by earlier jobs before resetting the Durable Objects.
    for (let completed = 0; completed < jobs.length;) {
      const batch = jobs.slice(completed);
      completed = jobs.length;
      await Promise.all(batch);
    }
    expect((await records()).matches).toHaveLength(1);
    const callback = vi
      .mocked(fetch)
      .mock.calls.find(([url]) => String(url).includes('/webhooks/'));
    expect(callback?.[1]?.method).toBe('PATCH');
    expect(JSON.parse(callback?.[1]?.body as string).content).toContain('Manual ranked match');
  });
});

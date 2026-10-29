import { env, exports } from 'cloudflare:workers';
import { interactions } from '../src/discord/interactions';
import {
  abortAllDurableObjects,
  reset,
  runDurableObjectAlarm,
  runInDurableObject,
} from 'cloudflare:test';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { emptyModules, type Settings } from '../src/domain/config';
import { balance, type Action } from '../src/domain/play';
import {
  defaultRatingSettings,
  rateTeams,
  roundEven,
  type Player,
  type RatingSettings,
} from '../src/domain/rating';
import { commands, adminCommand, adminCommandRoute } from '../src/discord/commands.mjs';
import fixtures from './fixtures/legacy-m2.json';

const guild = '706320010368516198',
  otherGuild = '706320010368516199';
const channel = '706320010368516200',
  channel2 = '706320010368516201';
const appId = '100000000000000001';
const game = '4daebc0a-55e8-4d56-a17d-d94913644910',
  game2 = '4daebc0a-55e8-4d56-a17d-d94913644911';
const queue = '81da571b-6a4a-4e5d-8e32-f4050df35a2d',
  queue2 = '81da571b-6a4a-4e5d-8e32-f4050df35a2e';
const settings: Settings = {
  games: [
    { id: game, name: 'Age of Empires II', catalogId: 'aoe2' },
    { id: game2, name: 'Friends custom RTS', catalogId: null },
  ],
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
    {
      id: queue2,
      gameId: game2,
      channelId: channel2,
      name: 'Other',
      size: 2,
      enabled: true,
      checkIn: { enabled: false, seconds: 60 },
      teamMethod: 'captain',
      ratingEngine: 'aoe2',
      modules: emptyModules,
    },
  ],
};
const person = (n: number) => ({
  id: String(100000000000000100n + BigInt(n)),
  name: `Player ${n}`,
});
let seq = 100000000000001000n;
const stub = (g = guild) => env.COMMUNITIES.getByName(g);
async function setup(s = settings, g = guild) {
  return stub(g).save(g, 0, s, person(99).id, 'save');
}
async function act(kind: Action['kind'], n = 0, rest: Partial<Action> = {}, g = guild) {
  return stub(g).play(g, {
    id: String(seq++),
    actor: person(n),
    channelId: channel,
    admin: false,
    kind,
    ...rest,
  });
}
async function match(g = guild) {
  return (await stub(g).records(g)).matches[0];
}
async function fill(skip = false) {
  if (skip) {
    const s = structuredClone(settings);
    s.queues[0].checkIn.enabled = false;
    await setup(s);
  } else await setup();
  for (let n = 0; n < 4; n++) await act('add', n);
  return match();
}
async function expireCheckIn() {
  await runInDurableObject(stub(), async (_instance, state) => {
    const row = state.storage.sql
      .exec<{ config: string }>("SELECT config FROM matches WHERE state='check_in'")
      .toArray()[0];
    const m = JSON.parse(row.config);
    m.deadline = Date.now() - 1000;
    state.storage.sql.exec('UPDATE matches SET config=? WHERE id=?', JSON.stringify(m), m.id);
    state.storage.sql.exec(
      "UPDATE deadlines SET due=? WHERE kind IN ('check_in','reminder')",
      Date.now() - 1000,
    );
  });
  await runDurableObjectAlarm(stub());
}
beforeEach(() => {
  seq = 100000000000001000n;
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const req = new Request(input, init),
      url = new URL(req.url);
    if (url.origin !== 'https://discord.com') throw new Error('Unexpected destination.');
    if (url.pathname.endsWith('/users/@me/guilds'))
      return Response.json([{ id: guild, name: 'Friends', permissions: '32' }]);
    return Response.json({ id: '100000000000008888' });
  });
});
afterEach(async () => {
  await reset();
  vi.restoreAllMocks();
});

describe('preserved legacy calculations', () => {
  it('matches 60 independent old Elo fixtures including draws, placements, scales and streak boosts', () => {
    for (const f of fixtures.rating)
      expect(
        rateTeams(
          f.teams as [Player[], Player[]],
          f.winner as 0 | 1 | null,
          f.settings as RatingSettings,
        ),
      ).toEqual(f.expected);
  });
  it('matches 28 old captain allocations, including preference relaxation and unrestricted fallback', () => {
    for (const f of fixtures.balance)
      expect(balance(f.players, 'captain').map((t) => t.map((p) => p.id))).toEqual(f.expected);
    expect(roundEven(1000.5)).toBe(1000);
    expect(roundEven(1001.5)).toBe(1002);
  });
  it('bounds work at supported rosters and rejects fewer than two players', () => {
    expect(() => balance([person(0) as Player], 'captain')).toThrow();
    expect(() =>
      balance(
        Array.from({ length: 18 }, (_, i) => ({ ...person(i), rating: 1000 }) as Player),
        'captain',
      ),
    ).toThrow();
  });
  it.each(['captain', 'balanced', 'random'] as const)(
    'includes every odd-roster player once with %s teams',
    (method) => {
      for (const count of [3, 5, 7, 15]) {
        const players = Array.from({ length: count }, (_, n) => ({
          ...person(n),
          rating: 800 + n * 70,
          deviation: 200,
          wins: 0,
          losses: 0,
          draws: 0,
          streak: 0,
        }));
        const teams = balance(players, method);
        expect(teams.map((t) => t.length)).toEqual([Math.floor(count / 2), Math.ceil(count / 2)]);
        expect(
          teams
            .flat()
            .map((p) => p.id)
            .sort(),
        ).toEqual(players.map((p) => p.id).sort());
        for (const engine of ['aoe2', 'flat', 'glicko2', 'trueskill'] as const) {
          const rated = rateTeams(teams, 0, defaultRatingSettings, engine);
          expect(
            rated.flat().every((p) => Number.isFinite(p.rating) && Number.isFinite(p.deviation)),
          ).toBe(true);
          expect(rated[0].every((p) => p.wins === 1)).toBe(true);
          expect(rated[1].every((p) => p.losses === 1)).toBe(true);
        }
      }
    },
  );
});

describe('durable queue and match journey', () => {
  it('creates one roster on concurrent final-slot joins and removes selected players from other game queues', async () => {
    await setup();
    await act('add', 0, { channelId: channel2 });
    for (let i = 0; i < 3; i++) await act('add', i);
    const replies = await Promise.all([act('add', 3), act('add', 4)]);
    expect(replies.filter((r) => r.content.includes('match found'))).toHaveLength(1);
    const records = await stub().records(guild);
    expect(records.matches).toHaveLength(1);
    expect(records.matches[0].roster).toHaveLength(4);
    await runInDurableObject(stub(), (_instance, state) => {
      expect(state.storage.sql.exec('SELECT * FROM active_players').toArray()).toHaveLength(4);
      expect(
        state.storage.sql.exec('SELECT * FROM queue_entries WHERE queue_id=?', queue).toArray(),
      ).toHaveLength(1);
      expect(
        state.storage.sql.exec('SELECT * FROM queue_entries WHERE queue_id=?', queue2).toArray(),
      ).toHaveLength(0);
    });
  });
  it('blocks active players across games only within this community', async () => {
    await fill();
    await setup(settings, otherGuild);
    expect((await act('add', 0, { channelId: channel2 })).content).toContain('active match');
    expect((await act('add', 0, {}, otherGuild)).content).toContain('Joined');
  });
  it('manually starts below capacity with everyone and scopes admin skip-check-in to this start', async () => {
    await setup();
    for (let i = 0; i < 3; i++) await act('add', i);
    expect((await act('start', 9, { skipCheckIn: true })).content).toContain(
      'Only an administrator',
    );
    await act('start', 9, { admin: true, skipCheckIn: true });
    const m = await match();
    expect(m.state).toBe('playing');
    expect(m.roster).toHaveLength(3);
    expect(
      (await stub().read(guild)).settings.queues.find((q) => q.id === queue)!.checkIn.enabled,
    ).toBe(true);
    await runInDurableObject(stub(), (_i, s) =>
      expect(
        s.storage.sql.exec('SELECT user_id FROM queue_entries WHERE queue_id=?', queue).toArray(),
      ).toEqual([]),
    );
  });
  it('requires admin authority for other-player changes and seeds; rejects rating changes during active matches', async () => {
    await setup();
    expect((await act('add', 0, { target: person(1) })).content).toContain('administrator');
    expect((await act('seed', 0, { target: person(1), rating: 1600 })).content).toContain(
      'Manage Server',
    );
    await act('seed', 9, { target: person(0), rating: 1600, admin: true });
    await act('add', 9, { target: person(0), admin: true });
    for (let i = 1; i < 4; i++) await act('add', i);
    expect((await match()).captains[0]).toBe(person(0).id);
    expect(
      (await act('seed', 9, { target: person(0), rating: 1700, admin: true })).content,
    ).toContain('active match');
  });
  it('check-in accepts each selected player once, rejects outsiders, and permits concurrent clicks on the same roster version', async () => {
    const m = await fill();
    expect((await act('ready', 9, { matchId: m.id, version: 1 })).content).toContain(
      'Only selected',
    );
    await Promise.all([
      act('ready', 0, { matchId: m.id, version: 1 }),
      act('ready', 1, { matchId: m.id, version: 1 }),
      act('ready', 0, { matchId: m.id, version: 1 }),
    ]);
    expect((await match()).ready).toHaveLength(2);
    await act('ready', 2, { matchId: m.id, version: 1 });
    await act('ready', 3, { matchId: m.id, version: 1 });
    expect((await match()).state).toBe('playing');
    expect((await act('ready', 0, { matchId: m.id, version: 1 })).content).toContain('changed');
  });
  it('expires after restart, rejects late readiness, returns ready players and removes no-shows without a retry loop', async () => {
    const m = await fill();
    await act('ready', 0, { matchId: m.id });
    await abortAllDurableObjects();
    await expireCheckIn();
    expect((await match()).state).toBe('check_in_failed');
    expect((await act('ready', 1, { matchId: m.id })).content).toContain('already closed');
    await runInDurableObject(stub(), (_i, s) => {
      expect(s.storage.sql.exec('SELECT * FROM active_players').toArray()).toHaveLength(0);
      expect(
        s.storage.sql.exec('SELECT user_id FROM queue_entries WHERE queue_id=?', queue).toArray(),
      ).toEqual([{ user_id: person(0).id }]);
      expect(s.storage.sql.exec('SELECT * FROM deadlines').toArray()).toHaveLength(0);
    });
  });
  it('replays a command without repeating its effect and rejects reuse of its ID with changed input', async () => {
    await setup();
    const a: Action = {
      id: String(seq++),
      actor: person(0),
      channelId: channel,
      admin: false,
      kind: 'add',
    };
    expect(await stub().play(guild, a)).toEqual(await stub().play(guild, a));
    expect((await stub().play(guild, { ...a, target: person(1), admin: true })).content).toContain(
      'different input',
    );
    await runInDurableObject(stub(), (_i, s) =>
      expect(s.storage.sql.exec('SELECT * FROM queue_entries').toArray()).toHaveLength(1),
    );
  });
  it('a losing captain finalizes immediately once; a non-captain cannot report; results survive restart and permit rejoining', async () => {
    const m = await fill(true);
    const nonCaptain = m.roster.find((p) => !m.captains.includes(p.id))!;
    const number = (id: string) => Number(BigInt(id) - 100000000000000100n);
    expect((await act('loss', number(nonCaptain.id), { matchId: m.id })).content).toContain(
      'Only a team captain',
    );
    const captain = number(m.captains[0]);
    await act('loss', captain, { matchId: m.id });
    const first = await stub().records(guild);
    expect(first.matches[0].result).toBe('beta');
    expect(first.matches[0].changes).toHaveLength(4);
    expect(first.players.every((p) => p.wins + p.losses + p.draws === 1)).toBe(true);
    await abortAllDurableObjects();
    await act('loss', captain, { matchId: m.id });
    expect((await stub().records(guild)).players).toEqual(first.players);
    expect((await stub().records(guild)).changes).toHaveLength(4);
    expect((await act('add', 0)).content).toContain('Joined');
  });
  it('requires both captains to agree to a draw, replacing a conflicting prior agreement', async () => {
    const m = await fill(true),
      n = (id: string) => Number(BigInt(id) - 100000000000000100n);
    await act('draw', n(m.captains[0]), { matchId: m.id });
    expect((await match()).state).toBe('playing');
    await act('abort', n(m.captains[1]), { matchId: m.id });
    expect((await match()).state).toBe('playing');
    await act('draw', n(m.captains[1]), { matchId: m.id });
    await act('draw', n(m.captains[0]), { matchId: m.id });
    expect((await match()).result).toBe('draw');
    expect(
      (await stub().records(guild)).players.every((p) => p.draws === 1 && p.streak === 0),
    ).toBe(true);
  });
  it('both-captain abort releases players with no rating changes, and admin recovery also works during check-in', async () => {
    let m = await fill(true),
      n = (id: string) => Number(BigInt(id) - 100000000000000100n);
    await act('abort', n(m.captains[0]), { matchId: m.id });
    await act('abort', n(m.captains[1]), { matchId: m.id });
    expect((await match()).state).toBe('aborted');
    expect((await stub().records(guild)).changes).toHaveLength(0);
    for (let i = 0; i < 4; i++) await act('add', i);
    m = await match();
    await act('recover_abort', 9, { admin: true, matchId: m.id });
    expect((await match()).state).toBe('aborted');
  });
  it('keeps ratings and matches independent in two games and namespaces', async () => {
    await setup();
    await act('seed', 9, { admin: true, target: person(0), rating: 1600 });
    expect((await act('rank', 0, { channelId: channel2 })).content).toContain('Rating 1000');
    for (let i = 0; i < 4; i++) await act('add', i);
    for (let i = 4; i < 6; i++) await act('add', i, { channelId: channel2 });
    const records = await stub().records(guild);
    expect(records.matches).toHaveLength(2);
    expect(records.matches.find((m) => m.queue.gameId === game2)!.gameName).toBe(
      'Friends custom RTS',
    );
    expect((await stub().records(guild, game2)).players.every((p) => p.rating === 1000)).toBe(true);
  });
  it('preserves active settings and prevents moving/removing an occupied queue or removing player records', async () => {
    await setup();
    await act('add', 0);
    const after = structuredClone(settings);
    after.queues = [];
    expect(await stub().save(guild, 1, after, person(9).id, 'save')).toMatchObject({
      ok: false,
      reason: expect.stringContaining('Empty the waiting queue'),
    });
    expect((await stub().read(guild)).revision).toBe(1);
    const rename = structuredClone(settings);
    rename.games[0].name = 'Renamed';
    await stub().save(guild, 1, rename, person(9).id, 'save');
    expect((await act('rank')).content).toContain('Renamed');
  });
  it('enables Glicko2 and the random team mode', async () => {
    const config = structuredClone(settings);
    config.queues[0].ratingEngine = 'glicko2';
    await setup(config);
    expect((await act('add')).content).toContain('Joined');
    await act('remove');
    const random = structuredClone(config);
    random.queues[0].ratingEngine = 'aoe2';
    random.queues[0].teamMethod = 'random';
    await stub().save(guild, 1, random, person(9).id, 'save');
    expect((await act('add')).content).toContain('Joined');
  });
  it('keeps a game’s established rating engine when its queue is removed and recreated', async () => {
    await setup();
    await act('seed', 9, { admin: true, target: person(0), rating: 1200 });
    const empty = structuredClone(settings);
    empty.queues = [];
    expect(await stub().save(guild, 1, empty, person(9).id, 'save')).toMatchObject({ ok: true });
    const recreated = structuredClone(settings);
    recreated.queues[0].id = crypto.randomUUID();
    recreated.queues[0].ratingEngine = 'glicko2';
    expect(await stub().save(guild, 2, recreated, person(9).id, 'save')).toMatchObject({
      ok: false,
      reason: expect.stringContaining('explicit conversion'),
    });
    expect((await stub().read(guild)).revision).toBe(2);
  });
});

describe('delivery and signed surfaces', () => {
  it('coalesces queue changes and persists unsent work across restart', async () => {
    await setup();
    await act('add', 0);
    await act('add', 1);
    await abortAllDurableObjects();
    await runInDurableObject(stub(), (_i, s) => {
      s.storage.sql.exec("UPDATE outbox SET due=0 WHERE status='pending'");
    });
    await stub().flush();
    const posts = vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === 'POST');
    expect(posts).toHaveLength(1);
    const body = JSON.parse(posts[0][1]!.body as string);
    expect(body.embeds[0].title).toContain('2/4');
    expect(body.enforce_nonce).toBe(true);
    await runInDurableObject(stub(), (_i, s) =>
      expect(s.storage.sql.exec('SELECT * FROM outbox').toArray()).toHaveLength(0),
    );
  });
  it('stores rate limits and retries later without holding an interaction acknowledgement', async () => {
    await setup();
    await act('add', 0);
    vi.mocked(fetch).mockImplementationOnce(async () =>
      Response.json({ retry_after: 2, global: true }, { status: 429 }),
    );
    await runInDurableObject(stub(), (_i, s) => {
      s.storage.sql.exec("UPDATE outbox SET due=0 WHERE status='pending'");
    });
    await stub().flush();
    await runInDurableObject(stub(), (_i, s) => {
      const row = s.storage.sql
        .exec<{ due: number; error: string }>('SELECT due,error FROM outbox')
        .toArray()[0];
      expect(row.error).toBe('rate_limited');
      expect(row.due).toBeGreaterThan(Date.now());
    });
    const gate = env.DISCORD_GATE.getByName(env.DISCORD_APPLICATION_ID!);
    await runInDurableObject(gate, (_i, s) => {
      expect(s.storage.sql.exec('SELECT * FROM limits').toArray()).toContainEqual({
        route: '*',
        until_ms: expect.any(Number),
      });
    });
    expect(await gate.reserve('another_channel')).toBeGreaterThan(Date.now());
  });
  it('blocks missing-permission delivery, stops paid retries, and permits an explicit card refresh after repair', async () => {
    await setup();
    await act('add', 0);
    vi.mocked(fetch).mockImplementationOnce(async () => Response.json({}, { status: 403 }));
    await runInDurableObject(stub(), (_i, s) => {
      s.storage.sql.exec("UPDATE outbox SET due=0 WHERE status='pending'");
    });
    await stub().flush();
    expect((await stub().records(guild)).delivery[0]).toMatchObject({
      status: 'blocked',
      error: 'discord_403',
    });
    await runInDurableObject(stub(), async (_i, s) =>
      expect(await s.storage.getAlarm()).toBeNull(),
    );
    await act('queue');
    await runInDurableObject(stub(), (_i, s) => {
      s.storage.sql.exec("UPDATE outbox SET due=0 WHERE status='pending'");
    });
    await stub().flush();
    expect((await stub().records(guild)).delivery).toHaveLength(0);
  });
  it('reconciles an ambiguous create rather than duplicating its public card', async () => {
    await setup();
    await act('add', 0);
    const footer = `Community Games · queue:${queue}:g1`;
    vi.mocked(fetch)
      .mockImplementationOnce(async () => {
        throw new Error('response lost');
      })
      .mockImplementationOnce(async () =>
        Response.json([
          {
            id: '100000000000008888',
            author: { id: appId },
            embeds: [{ footer: { text: footer } }],
          },
        ]),
      );
    await runInDurableObject(stub(), (_i, s) => {
      s.storage.sql.exec("UPDATE outbox SET due=0 WHERE status='pending'");
    });
    await stub().flush();
    await runInDurableObject(stub(), (_i, s) => s.storage.sql.exec('UPDATE outbox SET due=0'));
    await runInDurableObject(stub(), (_i, s) => {
      s.storage.sql.exec("UPDATE outbox SET due=0 WHERE status='pending'");
    });
    await stub().flush();
    expect(vi.mocked(fetch).mock.calls.filter(([, i]) => i?.method === 'POST')).toHaveLength(1);
    expect(vi.mocked(fetch).mock.calls.filter(([, i]) => i?.method === 'PATCH')).toHaveLength(1);
  });
  it('defers signed slash adds promptly and clears the temporary response despite channel rate limits', async () => {
    await setup();
    vi.mocked(fetch).mockImplementation(async (input) =>
      String(input).includes('/webhooks/')
        ? new Response(null, { status: 204 })
        : Response.json({ retry_after: 2, global: true }, { status: 429 }),
    );
    const payload = {
      id: String(seq++),
      type: 2,
      application_id: appId,
      token: 'synthetic-interaction-token',
      guild_id: guild,
      channel_id: channel,
      member: { permissions: '0', user: { ...person(0), username: 'Player0' } },
      data: { name: 'add' },
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
    const start = Date.now();
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
      { waitUntil: (job) => jobs.push(job) },
    );
    expect(Date.now() - start).toBeLessThan(1000);
    expect(await response.json()).toMatchObject({
      type: 5,
      data: { flags: 64 },
    });
    await Promise.all(jobs);
    expect(
      vi.mocked(fetch).mock.calls.filter(([url]) => String(url).includes('/webhooks/'))[0]?.[1]
        ?.method,
    ).toBe('DELETE');
    await runInDurableObject(stub(), (_i, s) => {
      s.storage.sql.exec("UPDATE outbox SET due=0 WHERE status='pending'");
    });
    await stub().flush();
  });
  it('shows the same persisted result on the authorized web records API and denies a non-admin', async () => {
    const m = await fill(true),
      n = Number(BigInt(m.captains[0]) - 100000000000000100n);
    await act('loss', n, { matchId: m.id });
    const sessionId = 'a'.repeat(64);
    await env.AUTH.getByName(`session:${sessionId}`).saveSession({
      user: { id: person(99).id, username: 'admin', avatar: null },
      csrf: 'b'.repeat(64),
      accessToken: 'test-access',
      expiresAt: Date.now() + 60000,
    });
    const request = () =>
      exports.default.fetch(`https://setup.example/api/communities/${guild}/records`, {
        headers: { Cookie: `__Host-cg-session=${sessionId}` },
      });
    const response = await request();
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({
      matches: [{ id: m.id, state: 'finished', result: 'beta' }],
    });
    vi.mocked(fetch).mockImplementation(async (input, init) =>
      new URL(new Request(input, init).url).pathname.endsWith('/users/@me/guilds')
        ? Response.json([{ id: guild, permissions: '0' }])
        : Response.json({ id: '100000000000008888' }),
    );
    expect((await request()).status).toBe(403);
  });
  it('keeps command definitions within Discord constraints with admin visibility on privileged groups', () => {
    expect(commands.map((c) => c.name)).toEqual([
      'test',
      'admin',
      'civpick',
      'manage',
      'add',
      'remove',
      'queue',
      'lobby',
      'teams',
      'rally',
      'gold',
      'maps',
      'predictions',
      'rank',
      'leaderboard',
      'profile',
      'subme',
      'subfor',
      'subauto',
      'report',
    ]);
    for (const c of commands) {
      expect(c.description.length).toBeLessThanOrEqual(100);
      expect(c.options?.length ?? 0).toBeLessThanOrEqual(25);
    }
    const admin = adminCommand;
    const panel = commands.find((c) => c.name === 'admin')!;
    expect(panel.default_member_permissions).toBeNull();
    expect(panel.options?.every((o) => o.type !== 1 && o.type !== 2)).toBe(true);
    expect(admin.default_member_permissions).toBe('32');
    expect(admin.options.map((g) => g.name)).toEqual(['setup', 'queue', 'match', 'player']);
    for (const group of admin.options) {
      expect(group.type).toBe(2);
      for (const action of group.options) {
        expect(action.type).toBe(1);
        expect(adminCommandRoute(group.name, action.name)).not.toBeNull();
        expect(action.options?.every((o) => o.type !== 1 && o.type !== 2) ?? true).toBe(true);
      }
    }
    const characters = (o: any): number =>
      ['name', 'description', 'value'].reduce(
        (n, key) => n + (typeof o[key] === 'string' ? o[key].length : 0),
        0,
      ) + [...(o.options ?? []), ...(o.choices ?? [])].reduce((n, o) => n + characters(o), 0);
    expect(characters(admin)).toBeLessThanOrEqual(8000);
    expect(commands.find((c) => c.name === 'queue')!.options.map((o) => o.name)).toEqual([
      'show',
      'start',
    ]);
    const queueStart = commands
      .find((c) => c.name === 'queue')!
      .options.find((o) => o.name === 'start')!;
    expect('options' in queueStart && queueStart.options?.map((o) => o.name)).toEqual(['queue']);
    expect(defaultRatingSettings.initial).toBe(1000);
  });
});

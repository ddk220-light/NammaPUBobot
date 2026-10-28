import { env } from 'cloudflare:workers';
import { abortAllDurableObjects, runDurableObjectAlarm, runInDurableObject } from 'cloudflare:test';
import { describe, expect, it, vi } from 'vitest';
import {
  act,
  channel,
  game,
  guild,
  harness,
  person,
  queue,
  settings,
  setup,
  stub,
  flush,
} from './harness';
import { type TestRequest, type TestSession } from '../src/domain/testing';
import { testMembers } from '../src/discord/test-members';
import { testInteraction } from '../src/discord/test-interactions';
import { interactions } from '../src/discord/interactions';
import { commands } from '../src/discord/commands.mjs';
import { defaultMaps } from '../src/domain/maps';
import { matchCard } from '../src/discord/cards';
import { observeGamePlayers, selectionStats } from '../src/storage/game-records';
import type { Match } from '../src/domain/play';

harness();
let seq = 100000000008000000n;
const request = (op: TestRequest['op'], extra: Partial<TestRequest> = {}): TestRequest => ({
  id: String(seq++),
  actor: person(99),
  manager: true,
  channelId: channel,
  op,
  ...extra,
});
const test = (op: TestRequest['op'], extra: Partial<TestRequest> = {}) =>
  stub().testCommand(guild, request(op, extra));
const playerNumber = (id: string) => Number(BigInt(id) - BigInt(person(0).id));
async function matches(): Promise<Match[]> {
  return runInDurableObject(stub(), (_i, s) =>
    s.storage.sql
      .exec<{ config: string }>('SELECT config FROM matches ORDER BY created_at DESC,rowid DESC')
      .toArray()
      .map((r) => JSON.parse(r.config)),
  );
}
const match = async () => (await matches())[0];
async function data() {
  return runInDurableObject(stub(), (_i, s) =>
    Object.fromEntries(
      [
        'queue_entries',
        'active_players',
        'profiles',
        'rating_changes',
        'rating_details',
        'wallets',
        'gold_ledger',
        'participants',
        'control_audit',
        'profile_refs',
        'played_selections',
        'bet_participation',
        'rank_jobs',
      ].map((name) => [name, s.storage.sql.exec(`SELECT * FROM ${name}`).toArray()]),
    ),
  );
}
function discord(count = 24) {
  const original = vi.mocked(fetch).getMockImplementation()!;
  const members = Array.from({ length: count }, (_, n) => ({
    user: { id: person(n).id, username: person(n).name },
    roles: [] as string[],
  }));
  const handler = async (input: RequestInfo | URL, init?: RequestInit) => {
    const r = new Request(input, init),
      u = new URL(r.url);
    if (u.pathname === `/api/v10/guilds/${guild}/members`)
      return Response.json([
        ...members,
        { user: { id: person(80).id, username: 'Bot', bot: true }, roles: [] },
      ]);
    return original(input, init);
  };
  vi.mocked(fetch).mockImplementation(handler);
  return { members, handler };
}
async function configured(autoStart = false) {
  const s = settings();
  s.queues[0].size = 8;
  s.queues[0].autoStart = autoStart;
  await setup(s);
  return s;
}
async function started(count = 8) {
  expect((await test('populate', { count })).ok).toBe(true);
  expect((await act('start', 99, { queue })).ok).not.toBe(false);
  return match();
}

describe('live queue test helpers', () => {
  it('acknowledges duplicate population even when no additional eligible members remain', async () => {
    discord(8);
    await configured();
    const input = request('populate');
    expect((await stub().testCommand(guild, input)).ok).toBe(true);
    const reads = vi.mocked(fetch).mock.calls.length;
    expect((await stub().testCommand(guild, input)).ok).toBe(true);
    expect(vi.mocked(fetch).mock.calls).toHaveLength(reads);
    expect((await data()).queue_entries).toHaveLength(8);
  });
  it('adds eight real humans to the live queue, preserving existing entries and regular buttons', async () => {
    discord();
    await configured();
    await act('add', 0);
    expect((await test('populate')).ok).toBe(true);
    const waiting = (await data()).queue_entries;
    expect(waiting).toHaveLength(9);
    expect(waiting[0].user_id).toBe(person(0).id);
    expect(new Set(waiting.map((p) => p.user_id)).size).toBe(9);
    expect(waiting.every((p) => p.user_id !== person(80).id)).toBe(true);
    expect(await matches()).toEqual([]);
    await flush();
    const payloads = vi
      .mocked(fetch)
      .mock.calls.filter(([, init]) => init?.method === 'POST' && init.body)
      .map(([, init]) => JSON.parse(String(init!.body)));
    expect(
      payloads.some((p) =>
        p.components?.some((r: any) =>
          r.components.some((b: any) => b.custom_id?.startsWith(`q:${queue}:join`)),
        ),
      ),
    ).toBe(true);
  });
  it('honors normal auto-start and population alone does not turn a match into a test', async () => {
    discord();
    await configured(true);
    await test('populate');
    const m = await match();
    expect(m.roster).toHaveLength(8);
    expect(m.state).toBe('check_in');
    expect(m.test).toBeUndefined();
    expect((await data()).queue_entries).toEqual([]);
    expect((await stub().records(guild)).matches).toHaveLength(1);
  });
  it('accepts an explicit queue name and leaves the other live queue unchanged', async () => {
    discord();
    const s = settings();
    s.queues[0].autoStart = false;
    s.queues[0].isDefault = true;
    const q2 = { ...s.queues[0], id: crypto.randomUUID(), name: 'Second', isDefault: false };
    s.queues.push(q2);
    await setup(s);
    await act('add', 0);
    expect((await test('populate', { queue: 'Second', count: 4 })).ok).toBe(true);
    const rows = (await data()).queue_entries;
    expect(rows.filter((r) => r.queue_id === queue)).toHaveLength(1);
    expect(rows.filter((r) => r.queue_id === q2.id)).toHaveLength(4);
  });
  it('skips active, queued, restricted and role-ineligible members', async () => {
    const d = discord();
    const s = await configured();
    s.queues[0].controls = {
      requiredRole: person(70).id,
      blockedRole: person(71).id,
      waitingSeconds: 0,
      matchSeconds: 0,
    };
    await stub().save(guild, 1, s, person(99).id, 'save');
    d.members.forEach((m) => {
      m.roles = [person(70).id];
    });
    d.members[0].roles = [];
    d.members[1].roles.push(person(71).id);
    await act('noadd', 99, { admin: true, target: person(2), seconds: 600 });
    expect((await test('populate')).ok).toBe(true);
    expect(
      (await data()).queue_entries.some((r) =>
        [person(0).id, person(1).id, person(2).id].includes(String(r.user_id)),
      ),
    ).toBe(false);
  });
  it('enforces current moderator roles and channel scope on both commands', async () => {
    discord();
    const s = await configured();
    expect((await test('populate', { manager: false })).ok).toBe(false);
    s.queues[0].adminRoles = [person(70).id];
    await stub().save(guild, 1, s, person(99).id, 'save');
    const moderator = { manager: false, actor: { ...person(99), roles: [person(70).id] } };
    expect((await test('populate', moderator)).ok).toBe(true);
    await act('start', 99);
    const m = await match();
    expect(
      (await test('checkin', { ...moderator, matchId: m.id, channelId: person(90).id })).ok,
    ).toBe(false);
    s.queues[0].adminRoles = [];
    await stub().save(guild, 2, s, person(99).id, 'save');
    expect((await test('checkin', { ...moderator, matchId: m.id })).ok).toBe(false);
    expect((await match()).test).toBeUndefined();
  });
  it.each(['flat', 'glicko2', 'trueskill', 'aoe2'] as const)(
    'regular captain reporting of a tagged %s match records no result or rating',
    async (engine) => {
      discord();
      const s = settings();
      s.queues[0].autoStart = false;
      s.queues[0].ratingEngine = engine;
      await setup(s);
      const m = await started(4);
      expect((await test('checkin', { matchId: m.id })).ok).toBe(true);
      const marked = await match();
      expect(marked.id).toBe(m.id);
      expect(marked.state).toBe('playing');
      expect(marked.ready).toHaveLength(4);
      expect(marked.test?.markedBy).toBe(person(99).id);
      const before = await data();
      expect((await act('loss', playerNumber(marked.captains[0]), { matchId: m.id })).ok).not.toBe(
        false,
      );
      const after = await data(),
        finished = await match();
      expect(finished.state).toBe('finished');
      expect(finished.result).toBeNull();
      expect(finished.changes).toEqual([]);
      expect(finished.test).toEqual(marked.test);
      for (const key of [
        'profiles',
        'rating_changes',
        'rating_details',
        'wallets',
        'gold_ledger',
        'rank_jobs',
      ])
        expect(after[key]).toEqual(before[key]);
      expect(after.participants).toEqual([]);
      expect(after.active_players).toEqual([]);
      expect(after.control_audit.some((r) => r.action === 'report')).toBe(false);
      expect((await stub().records(guild)).matches).toEqual([]);
      expect(matchCard(finished).embeds[0].title).toContain('TEST');
      expect(matchCard(finished).embeds[0].title).not.toContain('wins');
      expect((await act('undo', 99, { admin: true, matchId: m.id })).ok).toBe(false);
    },
  );
  it('uses ordinary map controls, keeps the TEST tag through a restart, and leaves votes for users', async () => {
    discord();
    const s = settings();
    s.queues[0].autoStart = false;
    s.queues[0].modules.maps = true;
    s.queues[0].maps = { ...defaultMaps(), countsAsCheckIn: true };
    await setup(s);
    const m = await started(4);
    await test('checkin', { matchId: m.id });
    let marked = await match();
    expect(marked.ready).toHaveLength(4);
    expect(marked.state).toBe('check_in');
    expect(marked.mapRound?.ballots).toEqual({});
    expect(
      matchCard(marked).components.some((r) =>
        r.components.some((c) => c.custom_id?.startsWith('mv:')),
      ),
    ).toBe(true);
    await abortAllDurableObjects();
    for (const p of marked.roster)
      expect(
        (
          await act('map_vote', playerNumber(p.id), {
            matchId: m.id,
            roundId: m.mapRound!.id,
            mapChoices: [m.mapRound!.choices[0]],
          })
        ).ok,
      ).not.toBe(false);
    marked = await match();
    expect(marked.state).toBe('playing');
    expect(marked.test).toBeTruthy();
    expect(
      (await act('admin_report', 99, { admin: true, matchId: m.id, result: 'alpha' })).ok,
    ).not.toBe(false);
    expect((await match()).result).toBeNull();
  });
  it('refunds existing predictions once and cannot pay out or reward test results', async () => {
    discord();
    const s = settings();
    s.queues[0].autoStart = false;
    s.queues[0].modules.gold = true;
    s.queues[0].modules.predictions = true;
    await setup(s);
    const m = await started(4);
    const a = playerNumber(m.teams[0][0].id),
      b = playerNumber(m.teams[1][0].id);
    expect((await act('bet', a, { matchId: m.id, side: 'alpha', stake: 50 })).ok).not.toBe(false);
    expect((await act('bet', b, { matchId: m.id, side: 'beta', stake: 50 })).ok).not.toBe(false);
    await act('bet_close', 99, { admin: true, matchId: m.id });
    expect((await data()).bet_participation).toHaveLength(2);
    expect((await test('checkin', { matchId: m.id })).ok).toBe(true);
    const before = await data();
    expect(before.bet_participation).toEqual([]);
    expect(before.wallets.every((w) => w.balance === 500)).toBe(true);
    expect((await act('bet', a, { matchId: m.id, side: 'alpha', stake: 50 })).ok).toBe(false);
    await test('checkin', { matchId: m.id });
    await act('admin_report', 99, { admin: true, matchId: m.id, result: 'beta' });
    const after = await data();
    expect(after.wallets).toEqual(before.wallets);
    expect(after.gold_ledger).toEqual(before.gold_ledger);
  });
  it('retains the marker through normal roster replacements and excludes the replacement’s result', async () => {
    discord();
    await configured();
    const m = await started();
    await test('checkin', { matchId: m.id });
    const outgoing = m.roster[0];
    expect(
      (
        await act('force_sub', 99, {
          admin: true,
          matchId: m.id,
          target: { id: outgoing.id, name: outgoing.name },
          replacement: person(60),
        })
      ).ok,
    ).not.toBe(false);
    expect((await match()).test).toBeTruthy();
    await act('admin_report', 99, { admin: true, matchId: m.id, result: 'draw' });
    expect((await data()).rating_changes).toEqual([]);
    expect((await match()).result).toBeNull();
  });
  it('requires an explicit match when several are active and rejects queue mismatches', async () => {
    discord();
    await configured();
    const first = await started();
    const second = await started();
    expect((await test('checkin')).ok).toBe(false);
    expect((await test('checkin', { matchId: first.id })).ok).toBe(true);
    const all = await matches();
    expect(all.find((m) => m.id === second.id)?.test).toBeUndefined();
    expect((await test('checkin', { matchId: first.id, queue: 'missing' })).ok).toBe(false);
  });
  it('does not retroactively mark finished results as tests and normal matches still rate', async () => {
    discord();
    await configured();
    const m = await started();
    await act('admin_report', 99, { admin: true, matchId: m.id, result: 'alpha' });
    const before = await data();
    expect(before.rating_changes).toHaveLength(8);
    expect((await test('checkin', { matchId: m.id })).ok).toBe(false);
    expect(await data()).toEqual(before);
    expect((await match()).result).toBe('alpha');
  });
  it('supports check-in-disabled and odd-sized matches using regular start controls', async () => {
    discord();
    const s = await configured();
    s.queues[0].checkIn.enabled = false;
    await stub().save(guild, 1, s, person(99).id, 'save');
    const m = await started(7);
    expect(m.teams.map((t) => t.length).sort()).toEqual([3, 4]);
    expect((await test('checkin', { matchId: m.id })).ok).toBe(true);
    expect((await match()).ready).toHaveLength(7);
  });
  it('does not duplicate population or reapply a delayed check-in to the next match', async () => {
    discord();
    await configured();
    const populate = request('populate');
    await Promise.all([stub().testCommand(guild, populate), stub().testCommand(guild, populate)]);
    expect((await data()).queue_entries).toHaveLength(8);
    await act('start', 99);
    const first = await match();
    const checkin = request('checkin');
    await stub().testCommand(guild, checkin);
    await act('admin_report', 99, { admin: true, matchId: first.id, result: 'alpha' });
    const second = await started();
    await stub().testCommand(guild, checkin);
    expect((await matches()).find((m) => m.id === second.id)?.test).toBeUndefined();
  });
  it('falls back to verified known members and fails population atomically when insufficient', async () => {
    const d = discord(1);
    await configured();
    expect((await test('populate')).ok).toBe(false);
    expect((await data()).queue_entries).toEqual([]);
    vi.mocked(fetch).mockImplementation(async (input, init) =>
      new URL(new Request(input, init).url).pathname.endsWith(`/guilds/${guild}/members`)
        ? new Response(null, { status: 403 })
        : d.handler(input, init),
    );
    expect(
      await testMembers(
        env,
        guild,
        channel,
        8,
        Array.from({ length: 10 }, (_, n) => person(n).id),
      ),
    ).toHaveLength(8);
  });
  it('excludes observed civilizations and inferred links for tagged matches', async () => {
    discord();
    await configured();
    const m = await started();
    await test('checkin', { matchId: m.id });
    const marked = await match();
    await runInDurableObject(stub(), (_i, state) => {
      expect(
        observeGamePlayers(state.storage.sql, marked, 'aoe2', { startedAt: Date.now() } as any),
      ).toEqual([]);
      expect(selectionStats(state.storage.sql, game, marked.roster[0].id)).toEqual([]);
    });
  });
  it('removes finished tests from admin history and retains their identifier for live operations', async () => {
    discord();
    await configured();
    const m = await started();
    await test('checkin', { matchId: m.id });
    await act('admin_report', 99, { admin: true, matchId: m.id, result: 'draw' });
    const result = await stub().adminPanel(guild, {
      id: String(seq++),
      actor: person(99),
      manager: true,
      channelId: channel,
      op: 'view',
    });
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.view.recent).toEqual([]);
      expect(result.view.latest?.id).toBe(m.id);
      expect(result.view.latest?.test).toBeTruthy();
    }
  });
  it('keeps legacy isolated-session cleanup working without exposing its retired commands', async () => {
    const old = stub(`test:${guild}:${queue}`);
    await runInDurableObject(old, async (i, state) => {
      i.read(guild);
      state.storage.kv.put('testMode', true);
      state.storage.kv.put('testSession', {
        id: crypto.randomUUID(),
        expiresAt: 0,
        cleaning: false,
      } satisfies TestSession);
    });
    await abortAllDurableObjects();
    const restarted = stub(`test:${guild}:${queue}`);
    await restarted.read(guild);
    await runDurableObjectAlarm(restarted);
    await runInDurableObject(restarted, (_i, state) =>
      expect(state.storage.kv.get('testSession')).toBeUndefined(),
    );
    expect(commands.find((c) => c.name === 'test')!.options.map((o: any) => o.name)).toEqual([
      'populate',
      'checkin',
    ]);
    for (const op of ['start', 'maps', 'result', 'status', 'reset']) {
      const i = {
        id: String(seq++),
        type: 2,
        application_id: env.DISCORD_APPLICATION_ID!,
        token: 'synthetic',
        guild_id: guild,
        channel_id: channel,
        member: { permissions: '8', user: { id: person(99).id } },
        data: { name: 'test', options: [{ name: op, type: 1 }] },
      };
      expect(((await testInteraction(i, env, { waitUntil() {} }).json()) as any).type).toBe(4);
    }
  });
  it('routes signed check-in through normal storage and removes the redundant private success receipt', async () => {
    discord();
    await configured();
    const m = await started();
    const raw = JSON.stringify({
      id: String(seq++),
      type: 2,
      application_id: env.DISCORD_APPLICATION_ID,
      guild_id: guild,
      channel_id: channel,
      token: 'synthetic-test-token',
      member: { permissions: '8', user: { id: person(99).id, username: 'Admin' } },
      data: {
        name: 'test',
        options: [{ name: 'checkin', type: 1, options: [{ name: 'match', type: 3, value: m.id }] }],
      },
    });
    const timestamp = String(Math.floor(Date.now() / 1000));
    // Public RFC 8032 fixture, never a real Discord signing key.
    const key = await crypto.subtle.importKey(
      'pkcs8',
      Uint8Array.from(
        '302e020100300506032b6570042204209d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60'
          .match(/../g)!
          .map((h) => parseInt(h, 16)),
      ),
      'Ed25519',
      false,
      ['sign'],
    );
    const signature = new Uint8Array(
      await crypto.subtle.sign('Ed25519', key, new TextEncoder().encode(timestamp + raw)),
    );
    const jobs: Promise<unknown>[] = [];
    const response = await interactions(
      new Request('https://setup.example/interactions', {
        method: 'POST',
        body: raw,
        headers: {
          'X-Signature-Timestamp': timestamp,
          'X-Signature-Ed25519': Array.from(signature, (b) => b.toString(16).padStart(2, '0')).join(
            '',
          ),
        },
      }),
      env,
      { waitUntil: (p) => jobs.push(p) },
    );
    expect(await response.json()).toMatchObject({ type: 5, data: { flags: 64 } });
    await Promise.all(jobs);
    expect((await match()).test).toBeTruthy();
    expect(
      vi
        .mocked(fetch)
        .mock.calls.some(
          ([input, init]) =>
            String(input).endsWith('/synthetic-test-token/messages/@original') &&
            init?.method === 'DELETE',
        ),
    ).toBe(true);
  });
});

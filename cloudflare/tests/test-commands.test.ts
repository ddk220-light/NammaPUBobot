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
} from './harness';
import {
  testObjectName,
  testSettings,
  type TestRequest,
  type TestSession,
} from '../src/domain/testing';
import { testMembers } from '../src/discord/test-members';
import { testInteraction } from '../src/discord/test-interactions';
import { interactions } from '../src/discord/interactions';
import { commands } from '../src/discord/commands.mjs';
import { defaultMaps } from '../src/domain/maps';
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
const sandbox = () => stub(testObjectName(guild, queue));
const test = (op: TestRequest['op'], extra: Partial<TestRequest> = {}) =>
  stub().testCommand(guild, request(op, extra));
const match = async () => (await sandbox().records(guild)).matches[0] as Match;
async function data(target = stub()) {
  return runInDurableObject(target, (_i, s) =>
    Object.fromEntries(
      [
        'queue_entries',
        'active_players',
        'profiles',
        'matches',
        'rating_changes',
        'rating_details',
        'wallets',
        'gold_ledger',
        'participants',
        'control_audit',
        'profile_refs',
      ].map((name) => [name, s.storage.sql.exec(`SELECT * FROM ${name}`).toArray()]),
    ),
  );
}
async function flushTest() {
  await runInDurableObject(sandbox(), (_i, s) =>
    s.storage.sql.exec("UPDATE outbox SET due=0 WHERE status='pending'"),
  );
  await sandbox().flush();
}
function discord(count = 12) {
  const original = vi.mocked(fetch).getMockImplementation()!;
  let id = 100000000004000000n;
  const messages: any[] = [];
  const members = Array.from({ length: count }, (_, n) => ({
    user: { id: person(n).id, username: person(n).name },
    roles: [],
  }));
  const handler = async (input: RequestInfo | URL, init?: RequestInit) => {
    const r = new Request(input, init),
      u = new URL(r.url);
    if (u.pathname === `/api/v10/guilds/${guild}/members`)
      return Response.json([
        ...members,
        { user: { id: person(80).id, username: 'Bot', bot: true }, roles: [] },
      ]);
    if (!u.pathname.includes(`/channels/${channel}/messages`)) return original(input, init);
    if (r.method === 'GET')
      return Response.json(messages.slice(0, Number(u.searchParams.get('limit'))));
    if (r.method === 'DELETE') {
      const index = messages.findIndex((m) => u.pathname.endsWith(`/${m.id}`));
      if (index >= 0) messages.splice(index, 1);
      return new Response(null, { status: index < 0 ? 404 : 204 });
    }
    const payload = (await r.json()) as Record<string, unknown>;
    if (r.method === 'POST') {
      const message = { ...payload, id: String(id++), author: { id: env.DISCORD_APPLICATION_ID } };
      messages.unshift(message);
      return Response.json(message);
    }
    const index = messages.findIndex((m) => u.pathname.endsWith(`/${m.id}`));
    if (index < 0) return new Response(null, { status: 404 });
    Object.assign(messages[index], payload);
    return Response.json(messages[index]);
  };
  vi.mocked(fetch).mockImplementation(handler);
  return { messages, members, handler };
}
async function configured(maps = false, count = 8) {
  const s = settings();
  s.queues[0].size = count;
  s.queues[0].modules.maps = maps;
  if (maps) s.queues[0].maps = defaultMaps();
  await setup(s);
  return s;
}

describe('isolated setup tests', () => {
  it('runs eight real members through queue, check-in and result without touching live data or writing ratings/history', async () => {
    const d = discord();
    const source = await configured();
    source.games[0].catalogId = 'aoe2';
    Object.assign(source.queues[0].modules, {
      gold: true,
      predictions: true,
      civs: true,
      stories: true,
    });
    await stub().save(guild, 1, source, person(99).id, 'save');
    await act('add', 0); // A sampled member can already be in the real queue.
    const before = await data();
    expect((await test('populate')).ok).toBe(true);
    expect((await data(sandbox())).queue_entries).toHaveLength(8);
    expect((await data(sandbox())).matches).toHaveLength(0); // No auto-start.
    await flushTest();
    expect((await test('start')).ok).toBe(true);
    await flushTest();
    expect((await match()).state).toBe('check_in');
    expect((await test('checkin')).ok).toBe(true);
    expect((await match()).ready).toHaveLength(8);
    expect((await match()).state).toBe('playing');
    expect((await test('result', { result: 'alpha' })).ok).toBe(true);
    await flushTest();
    const m = await match();
    expect(m.state).toBe('finished');
    expect(m.result).toBe('alpha');
    expect(m.changes).toEqual([]);
    const isolated = await data(sandbox());
    for (const name of [
      'rating_changes',
      'rating_details',
      'wallets',
      'gold_ledger',
      'participants',
      'control_audit',
      'profile_refs',
    ])
      expect(isolated[name]).toEqual([]);
    expect(await data()).toEqual(before);
    for (const card of d.messages.filter((m) => m.content?.includes('SETUP TEST'))) {
      expect(card.allowed_mentions).toEqual({ parse: [] });
      expect(card.components).toEqual([]);
      expect(card.embeds.every((e: any) => e.title.startsWith('TEST ·'))).toBe(true);
    }
    expect(d.messages.some((m) => m.content?.includes('SETUP TEST'))).toBe(true);
    await abortAllDurableObjects();
    expect((await data(sandbox())).participants).toEqual([]); // Constructor cannot backfill test history.
    expect((await test('reset')).ok).toBe(true);
    for (let i = 0; i < 4; i++) await flushTest();
    for (const rows of Object.values(await data(sandbox()))) expect(rows).toEqual([]);
    expect(await data()).toEqual(before);
    expect(d.messages.filter((m) => m.content?.includes('SETUP TEST'))).toEqual([]);
  });
  it('rejects non-moderators before fetching members and honors current queue roles', async () => {
    discord();
    const s = await configured();
    const calls = vi.mocked(fetch).mock.calls.length;
    expect((await test('populate', { manager: false })).ok).toBe(false);
    expect(vi.mocked(fetch).mock.calls).toHaveLength(calls);
    s.queues[0].adminRoles = [person(70).id];
    await stub().save(guild, 1, s, person(99).id, 'save');
    expect(
      (await test('populate', { manager: false, actor: { ...person(99), roles: [person(70).id] } }))
        .ok,
    ).toBe(true);
    s.queues[0].adminRoles = [];
    await stub().save(guild, 2, s, person(99).id, 'save');
    expect(
      (await test('start', { manager: false, actor: { ...person(99), roles: [person(70).id] } }))
        .ok,
    ).toBe(false);
    expect((await test('reset', { manager: false })).ok).toBe(false);
  });
  it('rejects commands from another channel and moderator roles belonging to another queue', async () => {
    discord();
    const s = await configured();
    s.queues.push({
      ...structuredClone(s.queues[0]),
      id: crypto.randomUUID(),
      name: 'Other',
      channelId: person(90).id,
      adminRoles: [person(70).id],
    });
    await stub().save(guild, 1, s, person(99).id, 'save');
    expect((await test('populate', { queue, channelId: person(90).id })).ok).toBe(false);
    expect(
      (await test('populate', { manager: false, actor: { ...person(99), roles: [person(70).id] } }))
        .ok,
    ).toBe(false);
    expect((await data()).matches).toEqual([]);
  });
  it.each(['flat', 'glicko2', 'trueskill', 'aoe2'] as const)(
    'does not change %s ratings or win counts even inside the sandbox',
    async (engine) => {
      discord();
      const s = settings();
      s.queues[0].ratingEngine = engine;
      s.queues[0].checkIn.enabled = false;
      await setup(s);
      await test('populate', { count: 4 });
      await test('start');
      const profiles = (await data(sandbox())).profiles;
      expect((await test('result', { result: 'beta' })).ok).toBe(true);
      const after = await data(sandbox());
      expect(after.profiles).toEqual(profiles);
      expect(after.rating_changes).toEqual([]);
      expect(after.participants).toEqual([]);
    },
  );
  it('cannot bootstrap test mode in live guild storage', async () => {
    const s = settings();
    await setup(s);
    expect(
      (
        await stub().runTest(
          guild,
          request('populate'),
          testSettings(s, s.queues[0]),
          Array.from({ length: 8 }, (_, n) => person(n)),
        )
      ).ok,
    ).toBe(false);
    expect((await data()).matches).toEqual([]);
  });
  it('is idempotent across duplicate starts, concurrent population and object restarts', async () => {
    discord();
    await configured();
    const input = request('populate');
    const results = await Promise.all([
      stub().testCommand(guild, input),
      stub().testCommand(guild, input),
    ]);
    expect(results.every((r) => r.ok !== false)).toBe(true);
    expect((await data(sandbox())).queue_entries).toHaveLength(8);
    const start = request('start');
    await stub().testCommand(guild, start);
    await abortAllDurableObjects();
    await stub().testCommand(guild, start);
    expect((await data(sandbox())).matches).toHaveLength(1);
    expect((await test('populate')).ok).toBe(false);
  });
  it.each(['vote', 'direct', 'random'] as const)(
    'uses the actual %s map lifecycle',
    async (mode) => {
      discord();
      const s = settings();
      s.queues[0].modules.maps = true;
      s.queues[0].maps = { ...defaultMaps(), mode, countsAsCheckIn: mode === 'vote' };
      await setup(s);
      await test('populate', { count: 4 });
      await test('start');
      await test('checkin');
      if (mode === 'direct') expect((await test('maps')).ok).toBe(true);
      expect((await match()).state).toBe('playing');
      expect((await match()).mapRound?.state).toBe('resolved');
    },
  );
  it('requires check-in and separate votes when voting does not count as check-in', async () => {
    discord();
    await configured(true);
    await test('populate');
    await test('start');
    expect((await test('result', { result: 'beta' })).ok).toBe(false);
    await test('checkin');
    expect((await match()).state).toBe('check_in');
    await test('maps');
    expect((await match()).state).toBe('playing');
    expect((await test('result', { result: 'draw' })).ok).toBe(true);
  });
  it('uses configured timers and supports odd rosters and disabled check-in', async () => {
    discord();
    const s = settings();
    s.queues[0].size = 8;
    s.queues[0].checkIn.seconds = 180;
    await setup(s);
    await test('populate', { count: 7 });
    await test('start');
    const m = await match();
    expect(m.teams.map((t) => t.length).sort()).toEqual([3, 4]);
    expect(m.deadline! - m.createdAt).toBe(180000);
    expect((await test('result', { result: 'abort' })).ok).toBe(true);
    await test('reset');
    for (let n = 0; n < 4; n++) await flushTest();
    s.queues[0].checkIn.enabled = false;
    await stub().save(guild, 1, s, person(99).id, 'save');
    await test('populate', { count: 2 });
    await test('start');
    expect((await match()).state).toBe('playing');
  });
  it('expires and erases the test even after a restart', async () => {
    discord();
    await configured();
    await test('populate');
    await test('start');
    await flushTest();
    await runInDurableObject(sandbox(), (_i, s) => {
      const session = s.storage.kv.get<TestSession>('testSession')!;
      session.expiresAt = 0;
      s.storage.kv.put('testSession', session);
    });
    await abortAllDurableObjects();
    await runDurableObjectAlarm(sandbox());
    for (let n = 0; n < 4; n++) await flushTest();
    expect((await data(sandbox())).matches).toEqual([]);
    expect((await test('status')).content).toContain('No test session');
  });
  it('cleans an uncertain message without reposting, and retains blocked cleanup for retry', async () => {
    const d = discord();
    await configured();
    await test('populate');
    let lost = false;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const response = await d.handler(input, init);
      if (init?.method === 'POST' && !lost) {
        lost = true;
        throw new Error('Lost receipt');
      }
      return response;
    });
    await flushTest();
    expect(lost).toBe(true);
    expect(d.messages).toHaveLength(1);
    vi.mocked(fetch).mockImplementation(async (input, init) =>
      init?.method === 'DELETE' ? new Response(null, { status: 403 }) : d.handler(input, init),
    );
    await test('reset');
    for (let n = 0; n < 3; n++) await flushTest();
    expect((await test('status')).content).toContain('blocked');
    expect((await data(sandbox())).matches).toEqual([]);
    expect((await test('populate')).ok).toBe(false);
    vi.mocked(fetch).mockImplementation(d.handler);
    await test('reset');
    for (let n = 0; n < 3; n++) await flushTest();
    expect(d.messages).toEqual([]);
    expect((await test('status')).content).toContain('No test session');
  });
  it('falls back to individually verified known members without privileged member listing', async () => {
    const d = discord();
    vi.mocked(fetch).mockImplementation(async (input, init) =>
      new URL(new Request(input, init).url).pathname.endsWith(`/guilds/${guild}/members`)
        ? new Response(null, { status: 403 })
        : d.handler(input, init),
    );
    const people = await testMembers(
      env,
      guild,
      channel,
      8,
      Array.from({ length: 10 }, (_, n) => person(n).id),
    );
    expect(people).toHaveLength(8);
    expect(new Set(people.map((p) => p.id)).size).toBe(8);
  });
  it('does not partially populate when there are too few current humans', async () => {
    discord(1);
    await configured();
    expect((await test('populate')).ok).toBe(false);
    expect((await data()).queue_entries).toEqual([]);
    expect((await test('status')).content).toContain('No test session');
  });
  it('never adopts or deletes a live queue card while recovering a lost test receipt', async () => {
    const d = discord();
    await configured();
    const live = {
      id: '100000000009999999',
      author: { id: env.DISCORD_APPLICATION_ID },
      embeds: [{ title: 'Pickup', description: 'Real players' }],
      components: [{ type: 1, components: [{ custom_id: `q:${queue}:join:g1` }] }],
    };
    d.messages.push(live);
    await test('populate');
    let lost = false;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const response = await d.handler(input, init);
      if (init?.method === 'POST' && !lost) {
        lost = true;
        throw new Error('Lost response');
      }
      return response;
    });
    await flushTest();
    expect(lost).toBe(true);
    // Put the real card first, as if chat activity had moved it above the test.
    d.messages.sort((a) => (a.id === live.id ? -1 : 1));
    vi.mocked(fetch).mockImplementation(d.handler);
    await flushTest();
    await test('reset');
    for (let n = 0; n < 4; n++) await flushTest();
    expect(d.messages).toEqual([live]);
    expect(live.embeds[0].title).toBe('Pickup');
  });
  it('routes a signed admin slash command through isolated storage and quietly removes its success receipt', async () => {
    discord();
    await configured();
    const raw = JSON.stringify({
      id: String(seq++),
      type: 2,
      application_id: env.DISCORD_APPLICATION_ID,
      guild_id: guild,
      channel_id: channel,
      token: 'synthetic-test-token',
      member: { permissions: '8', user: { id: person(99).id, username: 'Admin' } },
      data: { name: 'test', options: [{ name: 'populate', type: 1 }] },
    });
    const timestamp = String(Math.floor(Date.now() / 1000));
    // Public RFC 8032 fixture; never a real Discord signing key.
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
    expect((await data(sandbox())).queue_entries).toHaveLength(8);
    expect((await data()).queue_entries).toEqual([]);
    expect(
      vi
        .mocked(fetch)
        .mock.calls.some(
          ([input, init]) =>
            String(input).endsWith(
              '/webhooks/100000000000000001/synthetic-test-token/messages/@original',
            ) && init?.method === 'DELETE',
        ),
    ).toBe(true);
  });
  it('registers only constrained test options and rejects malformed or bot interactions', async () => {
    const command = commands.find((c) => c.name === 'test')!;
    expect(command.options.map((o: any) => o.name)).toEqual([
      'populate',
      'start',
      'checkin',
      'maps',
      'result',
      'status',
      'reset',
    ]);
    const i = {
      id: String(seq++),
      type: 2,
      application_id: env.DISCORD_APPLICATION_ID!,
      token: 'synthetic-test-token',
      guild_id: guild,
      channel_id: channel,
      member: { permissions: '8', user: { id: person(99).id } },
      data: {
        name: 'test',
        options: [{ name: 'populate', type: 1, options: [{ name: 'manager', value: true }] }],
      },
    };
    expect(((await testInteraction(i, env, { waitUntil() {} }).json()) as any).type).toBe(4);
    expect(
      (
        (await testInteraction(
          { ...i, member: { ...i.member, user: { ...i.member.user, bot: true } } },
          env,
          { waitUntil() {} },
        ).json()) as any
      ).type,
    ).toBe(4);
  });
});

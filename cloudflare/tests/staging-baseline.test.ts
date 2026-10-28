import { abortAllDurableObjects, runInDurableObject, runDurableObjectAlarm } from 'cloudflare:test';
import { describe, expect, it, vi } from 'vitest';
import {
  harness,
  fill,
  setup,
  settings,
  current,
  act,
  action,
  person,
  stub,
  guild,
  channel,
  queue,
} from './harness';
import type { Match } from '../src/domain/play';
import type { TestRequest } from '../src/domain/testing';
import { matchCard } from '../src/discord/cards';
import { commands } from '../src/discord/commands.mjs';

harness();
const request = (rest: Partial<TestRequest> = {}): TestRequest => ({
  id: action('queue').id,
  actor: person(99),
  manager: true,
  channelId: channel,
  op: 'checkin',
  ...rest,
});
const stored = (id: string): Promise<Match> =>
  runInDurableObject(stub(), (_i, s) =>
    JSON.parse(
      s.storage.sql
        .exec<{ config: string }>('SELECT config FROM matches WHERE id=?', id)
        .toArray()[0].config,
    ),
  );

describe('staging 0.18.1 behavioral reconciliation', () => {
  it('keeps /test populate and /test checkin registered as queue-moderated controls', () => {
    const test = commands.find((c) => c.name === 'test')!;
    expect(test.options?.map((o) => o.name)).toEqual(['populate', 'checkin']);
    expect(test.default_member_permissions).toBeNull();
  });
  it('marks a live match without recording result, ratings, participation, or prediction gains', async () => {
    const config = settings();
    config.queues[0].modules.gold = true;
    config.queues[0].modules.predictions = true;
    const m = await fill(config);
    await act('bet', 8, { matchId: m.id, side: 'alpha', stake: 50 });
    const before = (await stub().records(guild)).players;
    const input = request({ matchId: m.id });
    const reply = await stub().testCommand(guild, input);
    expect(reply).toMatchObject({ ok: true });
    expect(await stub().testCommand(guild, input)).toEqual(reply);
    let marked = await stored(m.id);
    expect(marked.test?.markedBy).toBe(person(99).id);
    expect(marked.ready).toHaveLength(4);
    expect(marked.state).toBe('playing');
    expect(matchCard(marked).embeds[0].title).toContain('TEST');
    await abortAllDurableObjects();
    const losingCaptain = Number(BigInt(m.captains[0]) - 100000000000000100n);
    expect((await act('loss', losingCaptain, { matchId: m.id })).content).toContain('not recorded');
    marked = await stored(m.id);
    expect(marked).toMatchObject({ state: 'finished', result: null, changes: [] });
    const records = await stub().records(guild);
    expect(records.matches).toEqual([]);
    expect(records.players).toEqual(before);
    expect(records.economy.books[0].status).toBe('refunded');
    expect(records.economy.wallets.find((w) => w.user_id === person(8).id)?.balance).toBe(500);
    await runInDurableObject(stub(), (_i, s) => {
      for (const table of [
        'participants',
        'rating_changes',
        'played_selections',
        'bet_participation',
      ])
        expect(s.storage.sql.exec(`SELECT * FROM ${table}`).toArray()).toEqual([]);
    });
    expect((await act('undo', 99, { admin: true, matchId: m.id })).content).toContain(
      'no recorded result',
    );
  });
  it('requires an authorized queue moderator and refuses a closed match', async () => {
    const m = await fill();
    expect((await stub().testCommand(guild, request({ manager: false, matchId: m.id }))).ok).toBe(
      false,
    );
    expect((await stored(m.id)).test).toBeUndefined();
    for (let n = 0; n < 4; n++) await act('ready', n, { matchId: m.id });
    await act('loss', Number(BigInt(m.captains[0]) - 100000000000000100n), { matchId: m.id });
    expect((await stub().testCommand(guild, request({ matchId: m.id }))).content).toContain(
      'Existing results',
    );
    expect((await current()).changes).toHaveLength(4);
  });
  it('accepts a queue moderator role without server management rights', async () => {
    const config = settings();
    const role = '100000000000008888';
    config.queues[0].adminRoles = [role];
    const m = await fill(config);
    expect(
      (
        await stub().testCommand(
          guild,
          request({ manager: false, actor: { ...person(99), roles: [role] }, matchId: m.id }),
        )
      ).ok,
    ).toBe(true);
  });
  it('populates verified real people once and refuses partial population', async () => {
    const config = settings();
    config.queues[0].autoStart = false;
    await setup(config);
    const old = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      if (String(input).includes(`/guilds/${guild}/members?`))
        return Response.json(
          [0, 1, 2, 3].map((n) => ({
            roles: [],
            user: { id: person(n).id, username: person(n).name },
          })),
        );
      return old(input, init);
    });
    const input = request({ op: 'populate', queue, count: 4 });
    expect((await stub().testCommand(guild, input)).ok).toBe(true);
    expect((await stub().testCommand(guild, input)).ok).toBe(true);
    expect((await stub().records(guild)).waiting[0].players).toHaveLength(4);
    expect((await stub().testCommand(guild, request({ op: 'populate', queue, count: 4 }))).ok).toBe(
      false,
    );
    expect((await stub().records(guild)).waiting[0].players).toHaveLength(4);
  });
  it('cleans up retired isolated test storage without deleting live community records', async () => {
    await fill();
    const live = await current();
    const isolated = stub('100000000000009999');
    await isolated.read('100000000000009999');
    await runInDurableObject(isolated, async (_i, s) => {
      s.storage.kv.put('testMode', true);
      s.storage.kv.put('testSession', { id: 'retired', expiresAt: 0 });
      s.storage.sql.exec(
        'INSERT INTO profiles VALUES(?,?,?)',
        live.queue.gameId,
        person(99).id,
        JSON.stringify(live.roster[0]),
      );
      await s.storage.setAlarm(Date.now());
    });
    await runDurableObjectAlarm(isolated);
    await runInDurableObject(isolated, (_i, s) => {
      expect(s.storage.sql.exec('SELECT * FROM profiles').toArray()).toEqual([]);
      expect(s.storage.kv.get('testSession')).toBeUndefined();
    });
    expect((await current()).id).toBe(live.id);
  });
});

import { runInDurableObject, runDurableObjectAlarm, abortAllDurableObjects } from 'cloudflare:test';
import { describe, it, expect, vi } from 'vitest';
import {
  harness,
  stub,
  settings,
  fill,
  current,
  act,
  person,
  guild,
  queue,
  flush,
  action,
} from './harness';
import { matchCard } from '../src/discord/cards';
import { settingsSchema } from '../src/domain/config';
harness();
describe('UX upgrade state and recovery', () => {
  it('a full role-filtered queue can recover with Join after member checks become available', async () => {
    const s = settings(),
      role = '100000000000000999';
    s.queues[0].controls = {
      waitingSeconds: 0,
      matchSeconds: 43200,
      requiredRole: role,
      blockedRole: null,
    };
    await stub().save(guild, 0, s, person(99).id, 'save');
    const normal = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (input, init) =>
      new Request(input, init).url.includes('/members/')
        ? new Response(null, { status: 503 })
        : normal(input, init),
    );
    for (let n = 0; n < 4; n++)
      await stub().play(guild, action('add', n, { actor: { ...person(n), roles: [role] } }));
    expect((await stub().records(guild)).waiting[0].players).toHaveLength(4);
    expect((await stub().records(guild)).matches).toHaveLength(0);
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const member = /\/members\/(\d+)$/.exec(new Request(input, init).url);
      return member
        ? Response.json({ roles: [role], user: { id: member[1] } })
        : normal(input, init);
    });
    const retry = await stub().play(
      guild,
      action('add', 0, { actor: { ...person(0), roles: [role] } }),
    );
    expect(retry.content).toContain('match found');
    expect((await current()).roster).toHaveLength(4);
  });
  it('preserves return overflow without taking existing waiters or automatically starting another game', async () => {
    const s = settings();
    s.queues[0].checkIn.enabled = false;
    const m = await fill(s);
    await runInDurableObject(stub(), (_i, state) => {
      for (let n = 4; n < 8; n++)
        state.storage.sql.exec(
          'INSERT INTO queue_entries(queue_id,user_id,display_name) VALUES(?,?,?)',
          queue,
          person(n).id,
          person(n).name,
        );
      state.storage.sql.exec("UPDATE deadlines SET due=0 WHERE kind='lifetime'");
    });
    await runDurableObjectAlarm(stub());
    const records = await stub().records(guild);
    expect(records.waiting[0].players.map((p) => p.id)).toEqual(
      [4, 5, 6, 7].map((n) => person(n).id),
    );
    expect(records.pendingReturns).toHaveLength(4);
    expect(records.pendingReturns.every((r) => r.status === 'overflow')).toBe(true);
    expect((await current()).id).toBe(m.id);
    await act('remove', 4);
    await runDurableObjectAlarm(stub());
    const after = await stub().records(guild);
    expect(after.waiting[0].players.map((p) => p.id)).toEqual(
      [5, 6, 7, 0].map((n) => person(n).id),
    );
    expect(after.pendingReturns).toHaveLength(3);
    expect((await current()).id).toBe(m.id);
  });
  it('keeps return tickets on a membership outage and lets an explicit leave cancel that intent', async () => {
    const s = settings();
    s.queues[0].checkIn.enabled = false;
    await fill(s);
    await runInDurableObject(stub(), (_i, state) =>
      state.storage.sql.exec("UPDATE deadlines SET due=0 WHERE kind='lifetime'"),
    );
    const normal = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (input, init) =>
      new Request(input, init).url.includes('/members/')
        ? new Response(null, { status: 503 })
        : normal(input, init),
    );
    await runDurableObjectAlarm(stub());
    expect((await stub().records(guild)).pendingReturns).toHaveLength(4);
    await act('remove', 0);
    expect(
      (await stub().records(guild)).pendingReturns.some((r) => r.user_id === person(0).id),
    ).toBe(false);
  });
  it('rejects ambiguous saved defaults as invalid settings', () => {
    const s = settings();
    s.queues[0].isDefault = true;
    s.queues.push({ ...s.queues[0], id: crypto.randomUUID(), name: 'Second' });
    expect(settingsSchema.safeParse(s).success).toBe(false);
  });
  it('reconciles the exact queue generation beyond the first history page', async () => {
    const s = settings();
    s.queues[0].autoStart = false;
    await stub().save(guild, 0, s, person(99).id, 'save');
    await act('add', 0);
    await runInDurableObject(stub(), (_i, state) => {
      state.storage.sql.exec("UPDATE cards SET reconcile=1 WHERE key LIKE 'queue:%:g%'");
    });
    let reads = 0;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const request = new Request(input, init);
      if (request.method === 'GET') {
        reads++;
        if (reads === 1)
          return Response.json(
            Array.from({ length: 100 }, (_, n) => ({
              id: String(100000000000200000n - BigInt(n)),
              author: { id: '100000000000000002' },
              embeds: [],
            })),
          );
        return Response.json([
          {
            id: '100000000000190000',
            author: { id: '100000000000000001' },
            embeds: [{ title: 'Pickup' }],
            components: [{ components: [{ custom_id: `q:${queue}:add:g1` }] }],
          },
        ]);
      }
      return Response.json({ id: '100000000000190000' });
    });
    await flush();
    await flush();
    expect(reads).toBe(2);
    expect(vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(
      0,
    );
  });
  it('reuses seven-ready style confirmations across failure, without extending their expiry', async () => {
    const m = await fill();
    for (let n = 0; n < 3; n++) await act('ready', n, { matchId: m.id });
    let expiry = 0;
    await runInDurableObject(stub(), (_i, s) => {
      expiry = s.storage.sql
        .exec<{ expires_at: number }>('SELECT expires_at FROM readiness ORDER BY user_id')
        .toArray()[0].expires_at;
      const row = s.storage.sql
        .exec<{ config: string }>('SELECT config FROM matches WHERE id=?', m.id)
        .toArray()[0];
      const state = JSON.parse(row.config);
      state.deadline = 0;
      s.storage.sql.exec('UPDATE matches SET config=? WHERE id=?', JSON.stringify(state), m.id);
      s.storage.sql.exec("UPDATE deadlines SET due=0 WHERE kind='check_in'");
    });
    await runDurableObjectAlarm(stub());
    await abortAllDurableObjects();
    await act('add', 4);
    const next = await current();
    expect(next.id).not.toBe(m.id);
    expect(next.ready).toEqual([person(0).id, person(1).id, person(2).id]);
    await runInDurableObject(stub(), (_i, s) =>
      expect(
        s.storage.sql
          .exec<{ expires_at: number }>('SELECT expires_at FROM readiness ORDER BY user_id')
          .toArray()[0].expires_at,
      ).toBe(expiry),
    );
    await act('ready', 4, { matchId: next.id });
    expect((await current()).state).toBe('playing');
  });
  it('does not renew readiness on repeat confirmations and clears it on back out', async () => {
    const m = await fill();
    await act('ready', 0, { matchId: m.id });
    let before = 0;
    await runInDurableObject(stub(), (_i, s) => {
      before = s.storage.sql
        .exec<{ expires_at: number }>('SELECT expires_at FROM readiness')
        .toArray()[0].expires_at;
    });
    await act('ready', 0, { matchId: m.id });
    await runInDurableObject(stub(), (_i, s) =>
      expect(
        s.storage.sql.exec<{ expires_at: number }>('SELECT expires_at FROM readiness').toArray()[0]
          .expires_at,
      ).toBe(before),
    );
    await act('back', 0, { matchId: m.id });
    await runInDurableObject(stub(), (_i, s) =>
      expect(s.storage.sql.exec('SELECT * FROM readiness').toArray()).toEqual([]),
    );
  });
  it('a timeout returns players, keeps a full queue on hold, and requires an explicit new action', async () => {
    const s = settings();
    s.queues[0].checkIn.enabled = false;
    const m = await fill(s);
    await runInDurableObject(stub(), (_i, state) => {
      state.storage.sql.exec("UPDATE deadlines SET due=0 WHERE kind='lifetime'");
    });
    await runDurableObjectAlarm(stub());
    expect((await current()).state).toBe('aborted');
    expect((await stub().records(guild)).waiting[0].players).toHaveLength(4);
    await runDurableObjectAlarm(stub());
    expect((await current()).id).toBe(m.id);
    expect((await act('add', 0)).content).toContain('match found');
    expect((await current()).id).not.toBe(m.id);
  });
  it('rejects a report past expiry even when the addressed match is beyond the sweep batch', async () => {
    const s = settings();
    s.queues[0].checkIn.enabled = false;
    const m = await fill(s);
    await runInDurableObject(stub(), (_i, state) => {
      const v = JSON.parse(
        state.storage.sql
          .exec<{ config: string }>('SELECT config FROM matches WHERE id=?', m.id)
          .toArray()[0].config,
      );
      v.expiresAt = 0;
      state.storage.sql.exec('UPDATE matches SET config=? WHERE id=?', JSON.stringify(v), m.id);
    });
    const n = Number(BigInt(m.captains[0]) - 100000000000000100n);
    expect((await act('loss', n, { matchId: m.id })).ok).toBe(false);
    expect((await current()).state).toBe('aborted');
    expect((await stub().records(guild)).changes).toEqual([]);
  });
  it('cancellation agrees only with the scoped current opposing captain; admin overrides', async () => {
    const s = settings();
    s.queues[0].checkIn.enabled = false;
    const m = await fill(s),
      n = (id: string) => Number(BigInt(id) - 100000000000000100n);
    await act('abort', n(m.captains[0]), { matchId: m.id });
    const p = (await current()).proposal!;
    expect((await act('abort', n(m.captains[0]), { matchId: m.id, proposalId: p.id })).ok).toBe(
      false,
    );
    expect((await act('abort', 9, { matchId: m.id, proposalId: p.id })).ok).toBe(false);
    await act('abort', n(m.captains[1]), { matchId: m.id, proposalId: p.id });
    expect((await current()).state).toBe('aborted');
    expect(matchCard(await current()).components).toEqual([]);
  });
  it('fresh publication replaces a confirmed card and no-op actions do not post', async () => {
    const s = settings();
    s.queues[0].autoStart = false;
    await stub().save(guild, 0, s, person(99).id, 'save');
    await act('add', 0);
    await flush();
    await act('add', 1);
    await flush();
    expect(vi.mocked(fetch).mock.calls.filter(([, i]) => i?.method === 'POST')).toHaveLength(2);
    expect(vi.mocked(fetch).mock.calls.filter(([, i]) => i?.method === 'DELETE')).toHaveLength(1);
    await act('add', 1);
    await flush();
    expect(vi.mocked(fetch).mock.calls.filter(([, i]) => i?.method === 'POST')).toHaveLength(2);
  });
});

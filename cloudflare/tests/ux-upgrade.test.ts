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

describe('match results, requests and quiet test matches', () => {
  const index = (id: string) => Number(BigInt(id) - 100000000000000100n);
  const playing = async () => {
    const s = settings();
    s.queues[0].checkIn.enabled = false;
    return fill(s);
  };
  const sent = (method: string) =>
    vi
      .mocked(fetch)
      .mock.calls.filter(([, init]) => (init?.method ?? 'GET') === method)
      .map(([url, init]) => ({ url: String(url), body: JSON.parse(String(init!.body)) }));
  const notes = (text: string) => sent('POST').filter((c) => c.body.content?.includes(text));
  const edits = (text: string) =>
    sent('PATCH').filter((c) => JSON.stringify(c.body.embeds ?? []).includes(text));
  async function due(kind: string, patch?: (m: any) => void) {
    await runInDurableObject(stub(), (_i, s) => {
      if (patch) {
        const row = s.storage.sql
          .exec<{ id: string; config: string }>('SELECT id,config FROM matches')
          .toArray()[0];
        const m = JSON.parse(row.config);
        patch(m);
        s.storage.sql.exec('UPDATE matches SET config=? WHERE id=?', JSON.stringify(m), row.id);
      }
      s.storage.sql.exec('UPDATE deadlines SET due=0 WHERE kind=?', kind);
    });
    await runDurableObjectAlarm(stub());
    await flush();
  }

  it('records an undisputed win claim at its deadline and tells the channel once', async () => {
    const m = await playing();
    const claim = await act('claim', index(m.captains[1]), { matchId: m.id });
    expect(claim.content).toContain(`Waiting for <@${m.captains[0]}> to confirm`);
    expect(await act('claim', index(m.captains[1]), { matchId: m.id })).toMatchObject({
      content: expect.stringContaining('Waiting for'),
    });
    const p = (await current()).proposal!;
    expect(p).toMatchObject({ kind: 'win', side: 'beta', opposing: m.captains[0] });
    await runInDurableObject(stub(), (_i, s) => {
      expect(
        s.storage.sql.exec("SELECT due FROM deadlines WHERE kind='proposal'").toArray()[0].due,
      ).toBe(p.expiresAt);
    });
    await flush();
    expect(notes('says Beta won')).toHaveLength(0);
    const request = sent('POST').find((c) =>
      c.body.embeds?.[0]?.description?.includes('says Beta won'),
    )!;
    expect(request.body.allowed_mentions).toEqual({ parse: [], users: [m.captains[0]] });
    await due('proposal', (saved) => (saved.proposal.expiresAt = Date.now() - 1));
    expect(await current()).toMatchObject({ state: 'finished', result: 'beta' });
    expect(edits('Nobody disputed within 10 minutes')).toHaveLength(1);
    await flush();
    const result = notes('Beta won. Ratings are updated on the match card.');
    expect(result).toHaveLength(1);
    expect(result[0].body).toMatchObject({ embeds: [], allowed_mentions: { parse: [] } });
  });

  it('expires and declines draw and cancellation requests without ending the match', async () => {
    const m = await playing();
    await act('draw', index(m.captains[0]), { matchId: m.id });
    const draw = (await current()).proposal!;
    expect(draw.expiresAt! - Date.now()).toBeGreaterThan(590000);
    await flush();
    const stale = await act('decline', index(m.captains[0]), {
      matchId: m.id,
      proposalId: draw.id,
    });
    expect(stale).toMatchObject({ ok: false, content: expect.stringContaining('Waiting for') });
    expect(
      await act('decline', index(m.captains[1]), { matchId: m.id, proposalId: draw.id }),
    ).toMatchObject({ content: 'Request declined. The match continues.' });
    expect(
      (await act('decline', index(m.captains[1]), { matchId: m.id, proposalId: draw.id })).content,
    ).toContain('already ended');
    await flush();
    expect(edits('declined. The match continues')).toHaveLength(1);
    await act('abort', index(m.captains[1]), { matchId: m.id });
    await flush();
    await due('proposal', (saved) => (saved.proposal.expiresAt = Date.now() - 1));
    const after = await current();
    expect(after.state).toBe('playing');
    expect(after.proposal).toBeUndefined();
    expect(edits('No answer within 10 minutes')).toHaveLength(1);
    expect(notes('Ratings are updated')).toHaveLength(0);
  });

  it('posts a result note per result, a withdrawal note on undo and a corrected note', async () => {
    const m = await playing();
    await act('loss', index(m.captains[0]), { matchId: m.id });
    await flush();
    await flush();
    expect(notes('**Pickup**: Beta won.')).toHaveLength(1);
    await act('undo', 99, { matchId: m.id, admin: true });
    await flush();
    expect(notes('the result was withdrawn')).toHaveLength(1);
    await act('admin_report', 99, { matchId: m.id, admin: true, result: 'alpha' });
    await flush();
    expect(notes('corrected result, Alpha won')).toHaveLength(1);
    expect(notes('**Pickup**:').every((n) => !n.body.allowed_mentions.users)).toBe(true);
  });

  it('posts no result note for a test match', async () => {
    const m = await playing();
    await stub().testCommand(guild, {
      id: action('queue').id,
      actor: person(99),
      manager: true,
      channelId: m.queue.channelId,
      op: 'checkin',
    });
    await act('loss', index(m.captains[0]), { matchId: m.id });
    await flush();
    expect(notes('**Pickup**:')).toHaveLength(0);
  });

  it('never mentions test-populated members in match-found messages, reminders or notices', async () => {
    const s = settings();
    s.queues[0].checkIn.seconds = 60;
    await stub().save(guild, 0, s, person(99).id, 'save');
    const normal = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (input, init) =>
      String(input).includes(`/guilds/${guild}/members?`)
        ? Response.json(
            [0, 1, 2, 3].map((n) => ({ roles: [], user: { id: person(n).id, username: 'M' } })),
          )
        : normal(input, init),
    );
    const reply = await stub().testCommand(guild, {
      id: action('queue').id,
      actor: person(99),
      manager: true,
      channelId: s.queues[0].channelId,
      op: 'populate',
      queue,
      count: 4,
    });
    expect(reply.ok).toBe(true);
    const m = await current();
    expect(m).toMatchObject({ state: 'check_in', silent: true });
    await flush();
    await due('reminder');
    const posts = sent('POST').filter((c) => c.url.endsWith('/messages'));
    expect(posts.length).toBeGreaterThanOrEqual(2);
    for (const post of posts) {
      expect(post.body.allowed_mentions.users ?? []).toEqual([]);
      expect(post.body.content ?? '').not.toMatch(/<@\d+>/);
    }
    await runInDurableObject(stub(), (_i, state) => {
      expect(state.storage.sql.exec('SELECT * FROM test_entries').toArray()).toEqual([]);
    });
  });

  it('keeps real joins announced after a test population was cleared', async () => {
    const m = await fill();
    expect(m.silent).toBeUndefined();
    await flush();
    const card = sent('POST').find((c) => c.body.embeds?.[0]?.title?.includes('check in'))!;
    expect(card.body.allowed_mentions.users).toHaveLength(4);
  });

  it('lets only organizers and moderators start a queue early unless set to anyone', async () => {
    const role = '100000000000000777';
    const s = settings();
    s.queues[0].size = 8;
    s.queues[0].adminRoles = [role];
    await stub().save(guild, 0, s, person(99).id, 'save');
    for (let n = 0; n < 3; n++) await act('add', n);
    const refused = await act('start', 5);
    expect(refused).toMatchObject({ ok: false });
    expect(refused.content).toContain('3/8 players');
    expect(refused.content).toContain('bot moderators can start it before it’s full');
    expect((await stub().records(guild)).matches).toHaveLength(0);
    expect(
      (await stub().play(guild, action('start', 5, { actor: { ...person(5), roles: [role] } }))).ok,
    ).not.toBe(false);
    expect((await stub().records(guild)).matches).toHaveLength(1);
    for (let n = 10; n < 13; n++) await act('add', n);
    expect((await act('start', 6, { admin: true })).ok).not.toBe(false);
    const saved = await stub().read(guild);
    saved.settings.queues[0].earlyStart = 'anyone';
    await stub().save(guild, saved.revision, saved.settings, person(99).id, 'save');
    for (let n = 20; n < 22; n++) await act('add', n);
    expect((await act('start', 7)).ok).not.toBe(false);
    expect((await stub().records(guild)).matches).toHaveLength(3);
  });

  it('still lets anyone start a full queue that does not start automatically', async () => {
    const s = settings();
    s.queues[0].autoStart = false;
    await stub().save(guild, 0, s, person(99).id, 'save');
    for (let n = 0; n < 4; n++) await act('add', n);
    expect((await act('start', 9)).ok).not.toBe(false);
    expect((await current()).roster).toHaveLength(4);
  });
});

import { runInDurableObject, runDurableObjectAlarm } from 'cloudflare:test';
import { describe, it, expect, vi } from 'vitest';
import { harness, stub, settings, fill, current, act, person, flush } from './harness';
harness();
const index = (id: string) => Number(BigInt(id) - 100000000000000100n);
async function remind() {
  await runInDurableObject(stub(), (_i, state) => {
    state.storage.sql.exec("UPDATE deadlines SET due=0 WHERE kind='reminder'");
  });
  await runDurableObjectAlarm(stub());
  await flush();
  return runInDurableObject(
    stub(),
    (_i, state) =>
      state.storage.sql
        .exec<{ key: string; message_id: string }>(
          "SELECT key,message_id FROM cards WHERE key LIKE 'reminder:%'",
        )
        .toArray()[0],
  );
}
function deleted(id: string) {
  return vi.mocked(fetch).mock.calls.some(([input, init]) => {
    const r = new Request(input, init);
    return r.method === 'DELETE' && new URL(r.url).pathname.endsWith(`/messages/${id}`);
  });
}
describe('temporary Discord messages follow their round', () => {
  it('does not send a queued reminder after its round ended', async () => {
    const m = await fill();
    await act('abort', 99, { matchId: m.id, admin: true });
    await runInDurableObject(stub(), (_i, state) => {
      const key = `reminder:${m.id}:${m.deadline}`;
      state.storage.sql.exec(
        'INSERT INTO cards(key,channel_id,nonce) VALUES(?,?,?)',
        key,
        m.queue.channelId,
        'late-reminder',
      );
      state.storage.sql.exec(
        'INSERT INTO outbox(key,payload,version,due) VALUES(?,?,1,0)',
        key,
        JSON.stringify({
          content: 'Check-in ends soon.',
          embeds: [],
          components: [],
          allowed_mentions: { parse: [] },
        }),
      );
    });
    await flush();
    expect(
      vi
        .mocked(fetch)
        .mock.calls.some(
          ([, init]) => typeof init?.body === 'string' && init.body.includes('Check-in ends'),
        ),
    ).toBe(false);
  });
  it('removes a delivered countdown when check-in expires', async () => {
    const m = await fill();
    const reminder = await remind();
    expect(reminder.message_id).toBeTruthy();
    await runInDurableObject(stub(), (_i, state) => {
      const expired = { ...m, deadline: Date.now() - 1000 };
      state.storage.sql.exec(
        'UPDATE matches SET config=? WHERE id=?',
        JSON.stringify(expired),
        m.id,
      );
      state.storage.sql.exec("UPDATE deadlines SET due=0 WHERE kind='check_in'");
    });
    await runDurableObjectAlarm(stub());
    await flush();
    expect((await current()).state).toBe('check_in_failed');
    expect(deleted(reminder.message_id)).toBe(true);
  });
  it('removes the reminder as soon as everyone is ready', async () => {
    await fill();
    const reminder = await remind();
    for (let n = 0; n < 4; n++) await act('ready', n);
    await flush();
    expect((await current()).state).toBe('playing');
    expect(deleted(reminder.message_id)).toBe(true);
  });
  it('retires the old countdown when a replacement starts another round', async () => {
    const m = await fill();
    await act('ready', 0);
    await act('add', 4);
    const reminder = await remind();
    await act('subauto', 1, { matchId: m.id });
    await flush();
    expect((await current()).state).toBe('check_in');
    expect((await current()).roster.some((p) => p.id === person(4).id)).toBe(true);
    expect(deleted(reminder.message_id)).toBe(true);
  });
  it('publishes a separate captain request and removes it after agreement', async () => {
    const s = settings();
    s.queues[0].checkIn.enabled = false;
    const m = await fill(s);
    await act('abort', index(m.captains[0]), { matchId: m.id });
    await flush();
    const p = (await current()).proposal!;
    const request = await runInDurableObject(
      stub(),
      (_i, state) =>
        state.storage.sql
          .exec<{ message_id: string }>(
            'SELECT message_id FROM cards WHERE key=?',
            `agreement:${m.id}:${p.id}`,
          )
          .toArray()[0],
    );
    expect(request.message_id).toBeTruthy();
    await act('abort', index(m.captains[1]), { matchId: m.id, proposalId: p.id });
    await flush();
    expect((await current()).state).toBe('aborted');
    expect(deleted(request.message_id)).toBe(true);
  });
  it('retires a superseded cancellation request when a captain asks for a draw', async () => {
    const s = settings();
    s.queues[0].checkIn.enabled = false;
    const m = await fill(s);
    await act('abort', index(m.captains[0]), { matchId: m.id });
    await flush();
    const old = (await current()).proposal!;
    const card = await runInDurableObject(
      stub(),
      (_i, state) =>
        state.storage.sql
          .exec<{ message_id: string }>(
            'SELECT message_id FROM cards WHERE key=?',
            `agreement:${m.id}:${old.id}`,
          )
          .toArray()[0],
    );
    await act('draw', index(m.captains[0]), { matchId: m.id });
    await flush();
    expect(deleted(card.message_id)).toBe(true);
    expect(
      (await act('abort', index(m.captains[1]), { matchId: m.id, proposalId: old.id })).ok,
    ).toBe(false);
  });
});

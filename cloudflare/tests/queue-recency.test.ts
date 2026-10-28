import { abortAllDurableObjects, runDurableObjectAlarm, runInDurableObject } from 'cloudflare:test';
import { describe, expect, it, vi } from 'vitest';
import {
  act,
  channel,
  current,
  flush,
  guild,
  harness,
  person,
  queue,
  settings,
  setup,
  stub,
} from './harness';

harness();

// Model Discord's relevant behavior: edits retain IDs and channel position;
// creations appear at the bottom, and history contains every public message.
function discord() {
  let sequence = 100000000005000000n;
  const messages: { id: string; payload: any }[] = [];
  const calls: { method: string; id?: string; limit?: string | null }[] = [];
  const normal = vi.mocked(fetch).getMockImplementation()!;
  const add = (payload: any = {}) => {
    const message = { id: String(sequence++), payload };
    messages.unshift(message);
    return message;
  };
  const handler = async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = new Request(input, init),
      url = new URL(request.url);
    const match = new RegExp(`/channels/${channel}/messages(?:/(\\d+))?$`).exec(url.pathname);
    if (!match) return normal(input, init);
    const id = match[1];
    calls.push({ method: request.method, id, limit: url.searchParams.get('limit') });
    if (request.method === 'GET')
      return Response.json(
        messages.slice(0, Number(url.searchParams.get('limit'))).map((m) => ({
          id: m.id,
          ...m.payload,
          author: { id: '100000000000000001' },
        })),
      );
    const index = messages.findIndex((m) => m.id === id);
    if (request.method === 'DELETE') {
      if (index < 0) return new Response(null, { status: 404 });
      messages.splice(index, 1);
      return new Response(null, { status: 204 });
    }
    const payload = await request.json();
    if (request.method === 'POST') return Response.json({ id: add(payload).id });
    if (index < 0) return new Response(null, { status: 404 });
    messages[index].payload = payload;
    return Response.json({ id });
  };
  vi.mocked(fetch).mockImplementation(handler);
  return { messages, calls, add, handler };
}

async function waiting() {
  const config = settings();
  config.queues[0].autoStart = false;
  await setup(config);
  await act('add', 0);
  await flush();
}

async function visibilityTick() {
  await runInDurableObject(stub(), (_i, state) => state.storage.kv.put('queueVisibilityDue', 0));
  await runDurableObjectAlarm(stub());
  await flush();
}

describe('periodic queue visibility', () => {
  it('recreates a manually deleted card without changing the roster', async () => {
    const d = discord();
    await waiting();
    const old = d.messages.shift()!;
    await visibilityTick();
    expect(d.messages).toHaveLength(1);
    expect(d.messages[0].id).not.toBe(old.id);
    expect(d.messages[0].payload.embeds).toEqual(old.payload.embeds);
  });

  it('does not restart a blocked publication or reset its retry budget', async () => {
    const d = discord();
    await waiting();
    await act('add', 1);
    await runInDurableObject(stub(), (_i, state) =>
      state.storage.sql.exec(
        "UPDATE outbox SET status='blocked',attempts=10,error='discord_403' WHERE key=?",
        `queue:${queue}:g2`,
      ),
    );
    d.calls.length = 0;
    await visibilityTick();
    expect(d.calls).toEqual([]);
    await runInDurableObject(stub(), (_i, state) =>
      expect(
        state.storage.sql
          .exec('SELECT status,attempts,error FROM outbox WHERE key=?', `queue:${queue}:g2`)
          .toArray(),
      ).toEqual([{ status: 'blocked', attempts: 10, error: 'discord_403' }]),
    );
  });

  it('cleans up a lost create receipt after opt-out without deleting the original card', async () => {
    const d = discord();
    await waiting();
    const original = d.messages[0];
    for (let n = 0; n < 5; n++) d.add();
    let lost = false;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const response = await d.handler(input, init);
      if (init?.method === 'POST' && !lost) {
        lost = true;
        throw new Error('Lost receipt');
      }
      return response;
    });
    await visibilityTick();
    expect(lost).toBe(true);
    expect(d.messages).toHaveLength(7);
    const s = await stub().read(guild);
    s.settings.queues[0].keepQueueVisible = false;
    await stub().save(guild, s.revision, s.settings, person(99).id, 'save');
    await abortAllDurableObjects();
    await flush();
    expect(d.messages).toHaveLength(6);
    expect(d.messages).toContain(original);
    expect(d.calls.filter((c) => c.method === 'POST')).toHaveLength(2); // Initial and uncertain replacement only.
  });

  it.each([1, 5])(
    'checks position %i without editing, deleting or creating a message',
    async (position) => {
      const d = discord();
      await waiting();
      const original = d.messages[0];
      for (let n = 1; n < position; n++) d.add();
      d.calls.length = 0;
      await visibilityTick();
      expect(d.calls).toEqual([{ method: 'GET', id: undefined, limit: '5' }]);
      expect(d.messages[position - 1]).toBe(original);
    },
  );

  it('moves a buried card without player activity and preserves all player data', async () => {
    const d = discord();
    await waiting();
    const before = await stub().operationalBackup(guild);
    const original = d.messages[0];
    for (let n = 0; n < 5; n++) d.add({ content: `Chat ${n}` });
    d.calls.length = 0;
    await visibilityTick();
    expect(d.calls.map((c) => c.method)).toEqual(['GET', 'GET', 'POST', 'DELETE']);
    expect(d.calls.at(-1)?.id).toBe(original.id);
    expect(d.messages).toHaveLength(6);
    expect(d.messages[0].payload.embeds).toEqual(original.payload.embeds);
    expect(d.messages[0].payload.allowed_mentions).toEqual({ parse: [] });
    const after = await stub().operationalBackup(guild);
    for (const table of [
      'queue_entries',
      'active_players',
      'matches',
      'profiles',
      'wallets',
      'gold_ledger',
      'queues',
      'games',
      'community',
    ])
      expect(after.tables[table]).toEqual(before.tables[table]);
  });

  it('keeps the 15-minute deadline through restart and does not check early', async () => {
    const d = discord();
    await waiting();
    const due = await runInDurableObject(stub(), (_i, state) =>
      state.storage.kv.get<number>('queueVisibilityDue')!,
    );
    expect(due - Date.now()).toBeGreaterThan(890000);
    expect(due - Date.now()).toBeLessThanOrEqual(900000);
    await abortAllDurableObjects();
    d.calls.length = 0;
    await runDurableObjectAlarm(stub());
    await flush();
    expect(d.calls).toEqual([]);
    await runInDurableObject(stub(), (_i, state) =>
      expect(state.storage.kv.get('queueVisibilityDue')).toBe(due),
    );
    await visibilityTick();
    expect(d.calls).toHaveLength(1);
    d.calls.length = 0;
    await runDurableObjectAlarm(stub());
    await flush();
    expect(d.calls).toEqual([]);
  });

  it('shares one history check for multiple queues in the same channel', async () => {
    const d = discord();
    const s = settings();
    s.queues.push({ ...structuredClone(s.queues[0]), id: crypto.randomUUID(), name: 'Second' });
    await setup(s);
    await act('queue', 0, { queue: 'Pickup' });
    await act('queue', 0, { queue: 'Second' });
    await flush();
    expect(d.messages).toHaveLength(2);
    d.calls.length = 0;
    await visibilityTick();
    expect(d.calls).toEqual([{ method: 'GET', id: undefined, limit: '5' }]);
  });

  it('persists the opt-out and leaves normal queue updates working', async () => {
    const d = discord();
    await waiting();
    const original = d.messages[0];
    const s = await stub().read(guild);
    s.settings.queues[0].keepQueueVisible = false;
    expect((await stub().save(guild, s.revision, s.settings, person(99).id, 'save')).ok).toBe(true);
    await abortAllDurableObjects();
    await runInDurableObject(stub(), async (_i, state) => {
      expect(state.storage.kv.get('queueVisibilityDue')).toBeUndefined();
      expect(await state.storage.getAlarm()).toBeNull();
    });
    for (let n = 0; n < 5; n++) d.add();
    d.calls.length = 0;
    await visibilityTick();
    expect(d.calls).toEqual([]);
    await act('add', 1);
    await flush();
    expect(d.messages[0].id).not.toBe(original.id);
    expect(d.messages[0].payload.embeds[0].title).toContain('2/4');
  });

  it.each(['unpublished', 'paused', 'recovery'])('does not poll %s queues', async (kind) => {
    const d = discord();
    if (kind === 'unpublished') await setup();
    else {
      await waiting();
      if (kind === 'paused') {
        const s = await stub().read(guild);
        s.settings.queues[0].enabled = false;
        await stub().save(guild, s.revision, s.settings, person(99).id, 'save');
        await flush();
      } else
        await runInDurableObject(stub(), (_i, state) =>
          state.storage.kv.put('recoveryReadOnly', true),
        );
    }
    d.calls.length = 0;
    await visibilityTick();
    expect(d.calls).toEqual([]);
    await runInDurableObject(stub(), async (_i, state) =>
      expect(await state.storage.getAlarm()).toBeNull(),
    );
  });

  it.each(['invalid', 'transport', 'forbidden', 'unavailable', 'rate-limit'])(
    'preserves cards on %s history and waits for the next check',
    async (kind) => {
      const d = discord();
      await waiting();
      const original = d.messages[0];
      for (let n = 0; n < 5; n++) d.add();
      vi.mocked(fetch).mockImplementation(async (input, init) => {
        if (new Request(input, init).method === 'GET') {
          if (kind === 'transport') throw new Error('Network failure');
          if (kind === 'invalid') return Response.json({ unexpected: true });
          return Response.json(
            { retry_after: 900, global: true },
            { status: kind === 'rate-limit' ? 429 : kind === 'forbidden' ? 403 : 503 },
          );
        }
        return d.handler(input, init);
      });
      d.calls.length = 0;
      await visibilityTick();
      expect(d.calls).toEqual([]);
      expect(d.messages).toContain(original);
      await runInDurableObject(stub(), (_i, state) => {
        expect(state.storage.kv.get<number>('queueVisibilityDue')! - Date.now()).toBeGreaterThan(
          890000,
        );
        expect(state.storage.sql.exec('SELECT * FROM outbox').toArray()).toHaveLength(0);
      });
      if (kind === 'rate-limit') {
        const { env } = await import('cloudflare:workers');
        const gate = env.DISCORD_GATE.getByName('100000000000000001');
        expect(await gate.reserve('channel:another')).toBeGreaterThan(Date.now());
        await runInDurableObject(gate, (_i, state) => state.storage.sql.exec('DELETE FROM limits'));
      }
      vi.mocked(fetch).mockImplementation(d.handler);
      await visibilityTick();
      expect(d.messages).not.toContain(original);
    },
  );

  it('cancels an undelivered periodic move when the setting is switched off', async () => {
    const d = discord();
    await waiting();
    const original = d.messages[0];
    for (let n = 0; n < 5; n++) d.add();
    await runInDurableObject(stub(), async (instance, state) => {
      state.storage.kv.put('queueVisibilityDue', 0);
      await (instance as any).playStore.refreshQueueVisibility(guild);
    });
    const s = await stub().read(guild);
    s.settings.queues[0].keepQueueVisible = false;
    await stub().save(guild, s.revision, s.settings, person(99).id, 'save');
    d.calls.length = 0;
    await flush();
    expect(d.calls.filter((c) => c.method !== 'GET')).toEqual([]);
    expect(d.messages).toContain(original);
  });

  it('coalesces a player update into a pending bump even when automatic visibility is then disabled', async () => {
    const d = discord();
    await waiting();
    for (let n = 0; n < 5; n++) d.add();
    await runInDurableObject(stub(), async (instance, state) => {
      state.storage.kv.put('queueVisibilityDue', 0);
      await (instance as any).playStore.refreshQueueVisibility(guild);
    });
    const s = await stub().read(guild);
    s.settings.queues[0].keepQueueVisible = false;
    await stub().save(guild, s.revision, s.settings, person(99).id, 'save');
    await act('add', 1);
    await flush();
    expect(d.messages[0].payload.embeds[0].title).toContain('2/4');
    expect(d.messages).toHaveLength(6);
  });
});

describe('queue card recency', () => {
  it.each([1, 5])(
    'edits a card at position %i without deleting it or moving it',
    async (position) => {
      const d = discord();
      await waiting();
      const original = d.messages[0];
      for (let n = 1; n < position; n++) d.add({ content: 'Community chat' });
      d.calls.length = 0;
      await act('add', 1);
      await flush();
      expect(d.calls).toEqual([
        { method: 'GET', id: undefined, limit: '5' },
        { method: 'PATCH', id: original.id, limit: null },
      ]);
      expect(d.messages[position - 1].id).toBe(original.id);
      expect(original.payload.embeds[0].title).toContain('2/4');
      expect(original.payload.embeds[0].description).toContain(person(1).id);
      await act('remove', 1);
      await flush();
      expect(original.payload.embeds[0].title).toContain('1/4');
      expect(d.calls.filter((c) => ['POST', 'DELETE'].includes(c.method))).toEqual([]);
    },
  );

  it('posts before deleting a card at position six, then edits the replacement', async () => {
    const d = discord();
    await waiting();
    const original = d.messages[0];
    for (let n = 0; n < 5; n++) d.add({ content: 'Community chat' });
    d.calls.length = 0;
    await act('add', 1);
    await flush();
    expect(d.calls.map((c) => c.method)).toEqual(['GET', 'POST', 'DELETE']);
    expect(d.calls[2].id).toBe(original.id);
    expect(d.messages).toHaveLength(6);
    const replacement = d.messages[0];
    expect(replacement.id).not.toBe(original.id);
    expect(replacement.payload.embeds[0].title).toContain('2/4');
    d.calls.length = 0;
    await act('add', 2);
    await flush();
    expect(d.calls.map((c) => c.method)).toEqual(['GET', 'PATCH']);
    expect(d.calls[1].id).toBe(replacement.id);
    expect(replacement.payload.embeds[0].title).toContain('3/4');
  });

  it.each(['unavailable', 'invalid', 'transport', 'rate-limit', 'forbidden'])(
    'preserves the original when history is %s and recovers without a duplicate',
    async (failure) => {
      const d = discord();
      await waiting();
      const original = d.messages[0];
      vi.mocked(fetch).mockImplementation(async (input, init) => {
        if (new URL(new Request(input, init).url).searchParams.get('limit') === '5') {
          if (failure === 'transport') throw new Error('Network unavailable');
          if (failure === 'invalid') return Response.json({ unexpected: true });
          if (failure === 'rate-limit') return Response.json({ retry_after: 1 }, { status: 429 });
          return new Response(null, { status: failure === 'forbidden' ? 403 : 503 });
        }
        return d.handler(input, init);
      });
      d.calls.length = 0;
      await act('add', 1);
      await flush();
      expect(d.calls).toEqual([]);
      expect(d.messages[0].id).toBe(original.id);
      expect(original.payload.embeds[0].title).toContain('1/4');
      expect((await stub().records(guild)).waiting[0].players).toHaveLength(2);
      await runInDurableObject(stub(), (_i, state) => {
        expect(
          state.storage.sql
            .exec<{ reconcile: number }>(
              'SELECT reconcile FROM cards WHERE key=?',
              `queue:${queue}:g2`,
            )
            .toArray()[0].reconcile,
        ).toBe(0);
      });
      vi.mocked(fetch).mockImplementation(d.handler);
      if (failure === 'rate-limit') {
        const { env } = await import('cloudflare:workers');
        await runInDurableObject(env.DISCORD_GATE.getByName('100000000000000001'), (_i, state) => {
          state.storage.sql.exec('DELETE FROM limits');
        });
      }
      await act('queue'); // Explicit refresh can recover a permissions block.
      await flush();
      expect(d.calls.map((c) => c.method)).toEqual(['GET', 'PATCH']);
      expect(original.payload.embeds[0].title).toContain('2/4');
    },
  );

  it('retries an accepted edit after a lost response and restart using the same ID', async () => {
    const d = discord();
    await waiting();
    const original = d.messages[0];
    let fail = true;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const response = await d.handler(input, init);
      if (init?.method === 'PATCH' && fail) {
        fail = false;
        throw new Error('Lost response');
      }
      return response;
    });
    d.calls.length = 0;
    await act('add', 1);
    await flush();
    await abortAllDurableObjects();
    await flush();
    expect(d.calls.map((c) => c.method)).toEqual(['GET', 'PATCH', 'PATCH']);
    expect(d.messages).toHaveLength(1);
    expect(d.messages[0].id).toBe(original.id);
    expect(original.payload.embeds[0].title).toContain('2/4');
  });

  it.each([false, true])(
    'reconciles an accepted replacement before cleanup (newer roster published: %s)',
    async (newer) => {
      const d = discord();
      await waiting();
      const original = d.messages[0];
      for (let n = 0; n < 5; n++) d.add({ content: 'Community chat' });
      let fail = true;
      vi.mocked(fetch).mockImplementation(async (input, init) => {
        const response = await d.handler(input, init);
        if (init?.method === 'POST' && fail) {
          fail = false;
          throw new Error('Lost create receipt');
        }
        return response;
      });
      d.calls.length = 0;
      await act('add', 1);
      await flush();
      const unconfirmed = d.messages[0];
      expect(d.messages.some((m) => m.id === original.id)).toBe(true);
      expect(d.calls.map((c) => c.method)).toEqual(['GET', 'POST']);
      if (newer) {
        await act('add', 2);
        await runInDurableObject(stub(), (_i, state) => {
          state.storage.sql.exec(
            'UPDATE outbox SET due=? WHERE key=?',
            Date.now() + 60000,
            `queue:${queue}:g2`,
          );
          state.storage.sql.exec('UPDATE outbox SET due=0 WHERE key=?', `queue:${queue}:g3`);
        });
        await stub().flush();
      }
      await abortAllDurableObjects();
      await flush();
      expect(d.calls.filter((c) => c.method === 'POST')).toHaveLength(newer ? 2 : 1);
      expect(d.messages).toHaveLength(6); // Five unrelated chat messages and one queue card.
      expect(d.messages[0].payload.embeds[0].title).toContain(newer ? '3/4' : '2/4');
      expect(d.messages.some((m) => m.id === unconfirmed.id)).toBe(!newer);
      expect(d.messages.some((m) => m.id === original.id)).toBe(false);
    },
  );

  it('recreates a card deleted between history lookup and edit', async () => {
    const d = discord();
    await waiting();
    const original = d.messages[0];
    let removed = false;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      if (init?.method === 'PATCH' && !removed) {
        d.messages.splice(0, 1);
        removed = true;
      }
      return d.handler(input, init);
    });
    await act('add', 1);
    await flush();
    await flush();
    expect(d.messages).toHaveLength(1);
    expect(d.messages[0].id).not.toBe(original.id);
    expect(d.messages[0].payload.embeds[0].title).toContain('2/4');
  });

  it.each(['history', 'edit'])(
    'discards a delayed old %s without overwriting the newer roster or deleting its shared card',
    async (failure) => {
      const d = discord();
      await waiting();
      let fail = true;
      vi.mocked(fetch).mockImplementation(async (input, init) => {
        const request = new Request(input, init);
        if (
          fail &&
          (failure === 'edit'
            ? request.method === 'PATCH'
            : new URL(request.url).searchParams.get('limit') === '5')
        ) {
          fail = false;
          throw new Error('Network unavailable');
        }
        return d.handler(input, init);
      });
      await act('add', 1);
      await flush();
      await runInDurableObject(stub(), (_i, state) => {
        state.storage.sql.exec(
          'UPDATE outbox SET due=? WHERE key=?',
          Date.now() + 60000,
          `queue:${queue}:g2`,
        );
      });
      await act('add', 2);
      await runInDurableObject(stub(), (_i, state) => {
        state.storage.sql.exec('UPDATE outbox SET due=0 WHERE key=?', `queue:${queue}:g3`);
      });
      await stub().flush();
      expect(d.messages[0].payload.embeds[0].title).toContain('3/4');
      d.calls.length = 0;
      await flush();
      expect(d.calls).toEqual([]);
      expect(d.messages).toHaveLength(1);
      expect(d.messages[0].payload.embeds[0].title).toContain('3/4');
    },
  );

  it('uses recency for explicit refresh but posts the empty queue below a new match', async () => {
    const d = discord();
    await setup();
    await act('add', 0);
    await flush();
    const original = d.messages[0];
    for (let n = 0; n < 5; n++) d.add();
    await act('queue');
    await flush();
    expect(d.messages.some((m) => m.id === original.id)).toBe(false);
    const replacement = d.messages[0];
    for (let n = 1; n < 4; n++) await act('add', n);
    await flush();
    expect((await current()).roster).toHaveLength(4);
    expect(d.messages[0].payload.embeds[0].title).toContain('0/4');
    expect(d.messages[1].payload.content).toContain('Check in below');
    expect(d.messages.some((m) => m.id === replacement.id)).toBe(false);
    expect(
      d.messages.filter((m) =>
        m.payload.components?.some((row: any) =>
          row.components.some((button: any) => button.custom_id?.startsWith(`q:${queue}:`)),
        ),
      ),
    ).toHaveLength(1);
  });
});

describe('queue placement after a match starts', () => {
  it('upgrades existing publications with no ordering dependencies and preserves the roster', async () => {
    const d = discord();
    await waiting();
    const old = d.messages[0].id;
    await runInDurableObject(stub(), (_i, state) =>
      state.storage.sql.exec('ALTER TABLE queue_publications DROP COLUMN after_matches'),
    );
    await abortAllDurableObjects();
    expect((await stub().records(guild)).waiting[0].players.map((p) => p.id)).toEqual([
      person(0).id,
    ]);
    await runInDurableObject(stub(), (_i, state) => {
      expect(
        state.storage.sql.exec('SELECT after_matches FROM queue_publications').toArray(),
      ).toEqual([{ after_matches: '[]' }]);
    });
    await act('add', 1);
    await flush();
    expect(d.messages[0].id).toBe(old);
    expect(d.messages[0].payload.embeds[0].title).toContain('2/4');
  });
  it.each(['automatic', 'manual'])(
    'posts check-in first and the joinable queue second for %s starts',
    async (mode) => {
      const d = discord();
      const config = settings();
      config.queues[0].autoStart = mode === 'automatic';
      await setup(config);
      await act('add', 0);
      await flush();
      const old = d.messages[0];
      const count = mode === 'automatic' ? 4 : 3;
      for (let n = 1; n < count; n++) await act('add', n);
      if (mode === 'manual') await act('start', 99);
      await flush();
      expect(d.messages).toHaveLength(2);
      expect(d.messages[0].payload.embeds[0].title).toContain('0/4');
      expect(d.messages[1].payload.content).toContain('Check in below');
      expect(d.messages.some((m) => m.id === old.id)).toBe(false);
      const nextQueue = d.messages[0].id;
      await act('add', 9);
      await flush();
      expect(d.messages[0].id).toBe(nextQueue);
      expect(d.messages[0].payload.embeds[0].title).toContain('1/4');
      expect((await current()).roster).toHaveLength(count);
    },
  );

  it.each(['server_error', 'lost_receipt', 'blocked'])(
    'waits for the match card through %s, new joins and restart',
    async (failure) => {
      const d = discord();
      await setup();
      await act('add', 0);
      await flush();
      const old = d.messages[0];
      let failed = false;
      vi.mocked(fetch).mockImplementation(async (input, init) => {
        const req = new Request(input, init);
        if (!failed && req.method === 'POST' && String(init?.body).includes('m:')) {
          failed = true;
          if (failure === 'lost_receipt') {
            await d.handler(input, init);
            throw new Error('Receipt lost');
          }
          return new Response(null, { status: failure === 'blocked' ? 403 : 503 });
        }
        return d.handler(input, init);
      });
      for (let n = 1; n < 4; n++) await act('add', n);
      await flush();
      expect(failed).toBe(true);
      expect(d.messages).toContain(old);
      expect(d.messages.filter((m) => m.payload.embeds?.[0]?.title?.includes('/4'))).toHaveLength(
        1,
      );
      await act('add', 9);
      await abortAllDurableObjects();
      vi.mocked(fetch).mockImplementation(d.handler);
      const match = await current();
      await act('recover', 99, { admin: true, matchId: match.id });
      await flush();
      expect(d.messages).toHaveLength(2);
      expect(d.messages[0].payload.embeds[0].title).toContain('1/4');
      expect(d.messages[1].payload.embeds[0].title).toBeTruthy();
      expect(d.messages.some((m) => m.id === old.id)).toBe(false);
      expect((await stub().records(guild)).waiting[0].players.map((p) => p.id)).toEqual([
        person(9).id,
      ]);
    },
  );

  it('keeps the queue below all matches formed before publication, including blocked delivery recovery', async () => {
    const d = discord();
    await setup();
    await act('add', 0);
    await flush();
    for (let n = 1; n < 8; n++) await act('add', n);
    const matches = (await stub().records(guild)).matches;
    await runInDurableObject(stub(), (_i, state) =>
      state.storage.sql.exec(
        "UPDATE outbox SET status='blocked',error='discord_403' WHERE key=?",
        `match:${matches[0].id}`,
      ),
    );
    await flush();
    expect(d.messages[0].payload.embeds[0].title).not.toContain('0/4');
    await act('recover', 99, { admin: true, matchId: matches[0].id });
    await flush();
    expect(d.messages).toHaveLength(3);
    expect(d.messages[0].payload.embeds[0].title).toContain('0/4');
    expect(d.messages.slice(1).every((m) => m.payload.embeds?.length)).toBe(true);
  });

  it('reconciles a lost replacement receipt without duplicating the queue or deleting the match', async () => {
    const d = discord();
    await setup();
    await act('add', 0);
    await flush();
    let failed = false;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const response = await d.handler(input, init);
      if (!failed && init?.method === 'POST' && String(init.body).includes(`q:${queue}:`)) {
        failed = true;
        throw new Error('Queue receipt lost');
      }
      return response;
    });
    for (let n = 1; n < 4; n++) await act('add', n);
    await flush();
    expect(failed).toBe(true);
    await abortAllDurableObjects();
    await flush();
    expect(d.messages).toHaveLength(2);
    expect(d.messages[0].payload.embeds[0].title).toContain('0/4');
    expect(d.calls.filter((c) => c.method === 'POST')).toHaveLength(3); // Old queue, match, replacement.
  });
});

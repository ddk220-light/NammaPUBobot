import { abortAllDurableObjects, runInDurableObject } from 'cloudflare:test';
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

  it('applies the same recency policy to explicit refresh and the empty queue after match creation', async () => {
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
    expect(replacement.payload.embeds[0].title).toContain('0/4');
    expect(d.messages.some((m) => m.id === replacement.id)).toBe(true);
    expect(
      d.messages.filter((m) =>
        m.payload.components?.some((row: any) =>
          row.components.some((button: any) => button.custom_id?.startsWith(`q:${queue}:`)),
        ),
      ),
    ).toHaveLength(1);
  });
});

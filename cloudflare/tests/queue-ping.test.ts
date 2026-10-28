import { env } from 'cloudflare:workers';
import { abortAllDurableObjects, runInDurableObject } from 'cloudflare:test';
import { describe, expect, it, vi } from 'vitest';
import {
  harness,
  setup,
  settings,
  act,
  action,
  stub,
  guild,
  queue,
  person,
  flush,
} from './harness';

harness();
const voice = '706320010368516222',
  otherVoice = '706320010368516223',
  privateVoice = '706320010368516224';
const feed = () => env.VOICE_PRESENCE.getByName(env.DISCORD_APPLICATION_ID!);
async function presence(users: number[], extra = false) {
  await runInDurableObject(feed(), (i) => {
    const p = (i as any).snapshot;
    p.initialize(guild, [], {
      roles: [{ id: guild, permissions: '1024' }],
      channels: [
        { id: voice, type: 2 },
        { id: otherVoice, type: 2 },
        {
          id: privateVoice,
          type: 2,
          permission_overwrites: [{ id: guild, deny: '1024', allow: '0' }],
        },
      ],
    });
    for (const n of users)
      p.update(guild, person(n).id, n % 2 ? voice : otherVoice, { user: { id: person(n).id } });
    if (extra) {
      p.update(guild, person(90).id, voice, { user: { id: person(90).id, bot: true } });
      p.update(guild, person(91).id, privateVoice, { user: { id: person(91).id } });
      p.update(guild, person(92).id, voice); // Unknown identity.
    }
    p.healthy = true;
    (i as any).socket = {};
  });
}
async function start() {
  const s = settings();
  s.queues[0].voicePreview = true;
  s.queues[0].autoStart = false;
  await setup(s);
  await act('add', 0);
  await flush();
  vi.mocked(fetch).mockClear();
}
const ping = (n = 0, extra = {}) => stub().pingVoice(guild, action('queue', n, extra));
const invitations = () =>
  vi
    .mocked(fetch)
    .mock.calls.filter(
      ([url, init]) => String(url).includes('/channels/') && init?.method === 'POST',
    )
    .map(([, init]) => JSON.parse(init!.body as string))
    .filter((p) => p.content?.includes('soon if you'));

describe('queue Ping invitations', () => {
  it('invites all eligible public voice participants across channels', async () => {
    await start();
    await presence([0, 1, 2, 3, 4, 5, 6, 7, 8, 9], true);
    await runInDurableObject(stub(), (_i, state) => {
      state.storage.sql.exec(
        'INSERT INTO active_players VALUES(?,?)',
        person(8).id,
        crypto.randomUUID(),
      );
    });
    await act('noadd', 99, { admin: true, target: person(9), seconds: 600 });
    expect((await ping()).ok).not.toBe(false);
    await flush();
    const cards = invitations();
    expect(cards).toHaveLength(1);
    expect(cards[0].allowed_mentions).toEqual({
      parse: [],
      users: expect.arrayContaining([1, 2, 3, 4, 5, 6, 7].map((n) => person(n).id)),
    });
    expect(cards[0].allowed_mentions.users).toHaveLength(7);
    expect(cards[0].components[0].components).toEqual([
      { type: 2, style: 3, label: 'Join', custom_id: `q:${queue}:join` },
    ]);
    expect(cards[0].nonce).toBeTruthy();
    expect(cards[0].enforce_nonce).toBe(true);
    expect((await stub().records(guild)).waiting[0].players.map((p) => p.id)).toEqual([
      person(0).id,
    ]);
  });
  it('batches every target within Discord message limits instead of truncating the list', async () => {
    await start();
    await presence(Array.from({ length: 56 }, (_, n) => n));
    await ping();
    await flush();
    const cards = invitations(),
      ids = cards.flatMap((c) => c.allowed_mentions.users);
    expect(cards).toHaveLength(2);
    expect(new Set(ids).size).toBe(55);
    expect(ids).not.toContain(person(0).id);
    expect(
      cards.every((c) => c.content.length < 2000 && c.allowed_mentions.users.length <= 50),
    ).toBe(true);
  });
  it('deduplicates repeated interactions and serializes concurrent clicks under a durable cooldown', async () => {
    await start();
    await presence([1, 2]);
    const a = action('queue', 0);
    const results = await Promise.all([stub().pingVoice(guild, a), ping(1)]);
    expect(results.filter((r) => r.ok !== false)).toHaveLength(1);
    const winner = results[0].ok !== false ? a : null;
    if (winner)
      expect((await stub().pingVoice(guild, winner)).content).toContain('already accepted');
    await abortAllDurableObjects();
    await presence([1, 2]);
    expect((await ping()).content).toContain('recently');
    await flush();
    expect(invitations()).toHaveLength(1);
  });
  it('shares the cooldown across queues in the same community', async () => {
    const s = settings();
    s.queues[0].voicePreview = true;
    s.queues.push({ ...structuredClone(s.queues[0]), id: crypto.randomUUID(), name: 'Other' });
    await setup(s);
    await presence([1]);
    expect((await ping(0, { queue })).ok).not.toBe(false);
    expect((await ping(0, { queue: s.queues[1].id })).content).toContain('recently');
  });
  it('accepts simultaneous retries of the same click without duplicate notifications', async () => {
    await start();
    await presence([1]);
    const click = action('queue', 0);
    expect(
      (await Promise.all([stub().pingVoice(guild, click), stub().pingVoice(guild, click)])).every(
        (r) => r.ok !== false,
      ),
    ).toBe(true);
    await flush();
    expect(invitations()).toHaveLength(1);
  });
  it('honors Discord global rate limits during role verification without accepting a partial ping', async () => {
    const role = '100000000000001234',
      s = settings();
    s.queues[0].voicePreview = true;
    s.queues[0].controls = {
      requiredRole: role,
      blockedRole: null,
      waitingSeconds: 0,
      matchSeconds: 0,
    };
    await setup(s);
    await presence([1, 2]);
    vi.mocked(fetch).mockImplementation(async () =>
      Response.json({ retry_after: 2, global: true }, { status: 429 }),
    );
    const actor = { ...person(0), roles: [role] };
    expect((await ping(0, { actor })).content).toContain('busy');
    expect((await ping(0, { actor })).content).toContain('busy');
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);
    expect(
      await env.DISCORD_GATE.getByName(env.DISCORD_APPLICATION_ID!).reserve('another-channel'),
    ).toBeGreaterThan(Date.now());
    await runInDurableObject(stub(), (_i, state) =>
      expect(state.storage.sql.exec('SELECT * FROM rally_runs').toArray()).toHaveLength(0),
    );
  });
  it('excludes waiting players in other queues as well as its own', async () => {
    const s = settings();
    s.queues[0].voicePreview = true;
    s.queues.push({ ...structuredClone(s.queues[0]), id: crypto.randomUUID(), name: 'Other' });
    await setup(s);
    await act('add', 1, { queue: s.queues[1].id });
    await presence([1, 2]);
    await ping(0, { queue });
    await flush();
    expect(invitations()[0].allowed_mentions.users).toEqual([person(2).id]);
  });
  it('checks roles without requiring /rally permissions, and fails closed on member API errors', async () => {
    const role = '100000000000001234',
      s = settings();
    s.queues[0].voicePreview = true;
    s.queues[0].controls = {
      requiredRole: role,
      blockedRole: null,
      waitingSeconds: 0,
      matchSeconds: 0,
    };
    await setup(s);
    await presence([1, 2]);
    expect((await ping()).content).toContain('not eligible');
    const original = vi.mocked(fetch).getMockImplementation()!;
    let unavailable = true;
    vi.mocked(fetch).mockImplementation(async (url, init) => {
      const match = /\/members\/(\d+)$/.exec(String(url));
      if (match)
        return unavailable
          ? new Response('', { status: 503 })
          : Response.json({
              user: { id: match[1] },
              roles: match[1] === person(1).id ? [role] : [],
            });
      return original(url, init);
    });
    expect((await ping(0, { actor: { ...person(0), roles: [role] } })).ok).toBe(false);
    await runInDurableObject(stub(), (_i, state) =>
      expect(state.storage.sql.exec('SELECT * FROM rally_runs').toArray()).toHaveLength(0),
    );
    unavailable = false;
    expect((await ping(0, { actor: { ...person(0), roles: [role] } })).ok).not.toBe(false);
    await flush();
    expect(invitations()[0].allowed_mentions.users).toEqual([person(1).id]);
  });
  it('rechecks targets immediately before delivery and removes only those who no longer need an invitation', async () => {
    await start();
    await presence([1, 2, 3]);
    await ping();
    await act('add', 1);
    await presence([1, 3]);
    await flush();
    expect(invitations()[0].allowed_mentions.users).toEqual([person(3).id]);
  });
  it.each(['paused', 'disconnected', 'expired', 'full'])(
    'drops pending invitations when %s before sending',
    async (reason) => {
      await start();
      await presence([1, 2]);
      await ping();
      if (reason === 'paused') {
        const s = await stub().read(guild);
        s.settings.queues[0].enabled = false;
        await stub().save(guild, s.revision, s.settings, person(99).id, 'save');
      } else if (reason === 'disconnected') {
        await runInDurableObject(feed(), (i) => (i as any).snapshot.invalidate());
      } else if (reason === 'expired') {
        await runInDurableObject(stub(), (_i, state) =>
          state.storage.sql.exec('UPDATE rally_runs SET created_at=0'),
        );
      } else {
        for (let n = 1; n <= 3; n++) await act('add', n);
      }
      await flush();
      expect(invitations()).toHaveLength(0);
    },
  );
  it('does not enqueue invitations for disabled/recovery/foreign queues or empty/unavailable voice lists', async () => {
    await setup();
    await presence([1]);
    expect((await ping()).content).toContain('disabled');
    const s = await stub().read(guild);
    s.settings.queues[0].voicePreview = true;
    await stub().save(guild, s.revision, s.settings, person(99).id, 'save');
    expect((await ping(0, { channelId: '706320010368516299' })).content).toContain(
      'No matching queue',
    );
    await presence([]);
    expect((await ping()).content).toContain('No eligible people');
    await runInDurableObject(feed(), (i) => (i as any).snapshot.invalidate());
    expect((await ping()).content).toContain('unavailable');
    await runInDurableObject(stub(), (_i, state) => state.storage.kv.put('recoveryReadOnly', true));
    expect((await ping()).content).toContain('read-only');
    expect(invitations()).toHaveLength(0);
  });
  it('recovers a lost create receipt without sending or editing a second invitation', async () => {
    await start();
    await presence([1]);
    await ping();
    const original = vi.mocked(fetch).getMockImplementation()!;
    let sent: any;
    vi.mocked(fetch).mockImplementation(async (url, init) => {
      if (init?.method === 'POST' && String(url).includes('/channels/')) {
        const p = JSON.parse(init.body as string);
        if (p.content?.includes('soon if you')) {
          sent = p;
          throw Error('Lost receipt after create');
        }
      }
      if (!init?.method && String(url).includes('/messages?limit=100') && sent)
        return Response.json([
          {
            id: '100000000001999999',
            nonce: sent.nonce,
            author: { id: env.DISCORD_APPLICATION_ID },
          },
        ]);
      return original(url, init);
    });
    await flush();
    expect(sent).toBeTruthy();
    await flush();
    expect(invitations()).toHaveLength(1);
    expect(
      vi
        .mocked(fetch)
        .mock.calls.some(
          ([url, init]) => String(url).endsWith('/100000000001999999') && init?.method === 'PATCH',
        ),
    ).toBe(false);
  });
});

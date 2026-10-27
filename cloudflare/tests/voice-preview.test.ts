import { env, exports } from 'cloudflare:workers';
import { abortAllDurableObjects, runInDurableObject, runDurableObjectAlarm } from 'cloudflare:test';
import { describe, expect, it, vi } from 'vitest';
import app from '../src/worker';
import { PresenceSnapshot } from '../src/domain/presence';
import { queueCard } from '../src/discord/cards';
import { harness, settings, setup, act, stub, guild, queue, person, flush } from './harness';

harness();
const voice = '706320010368516222',
  restricted = '706320010368516223';
const feed = () => env.VOICE_PRESENCE.getByName(env.DISCORD_APPLICATION_ID!);
const member = (n: number, bot = false) => ({
  user: { id: person(n).id, ...(bot ? { bot: true } : {}) },
});
const metadata = () => ({
  roles: [{ id: guild, permissions: '1024' }],
  channels: [
    { id: voice, type: 2, permission_overwrites: [] },
    { id: restricted, type: 2, permission_overwrites: [{ id: guild, allow: '0', deny: '1024' }] },
  ],
});
async function occupants(numbers: number[]) {
  await runInDurableObject(feed(), (instance) => {
    const o = instance as any;
    o.snapshot.initialize(guild, [], metadata());
    for (const n of numbers) o.snapshot.update(guild, person(n).id, voice, member(n), 1000 + n);
    o.snapshot.healthy = true;
    o.socket = {};
  });
}
async function start() {
  const s = settings();
  s.queues[0].autoStart = false;
  s.queues[0].voicePreview = true;
  await setup(s);
  await act('add', 0);
  await flush();
}
async function tick() {
  await runInDurableObject(stub(), (_i, state) => {
    const cache = state.storage.kv.get<any>('queueVoice');
    if (cache) state.storage.kv.put('queueVoice', { ...cache, due: 0 });
  });
  await runDurableObjectAlarm(stub());
  await flush();
}
function latestCard() {
  return vi
    .mocked(fetch)
    .mock.calls.filter(([, init]) => ['POST', 'PATCH'].includes(init?.method ?? ''))
    .map(([, init]) => JSON.parse(init!.body as string))
    .filter((p) => p.embeds?.[0]?.title?.startsWith('Pickup ·'))
    .at(-1);
}

describe('voice snapshot ordering and visibility', () => {
  it('orders actual channel joins, without treating mute events or initial occupants as new joins', () => {
    const p = new PresenceSnapshot();
    p.initialize(guild, [{ user_id: person(0).id, channel_id: voice }], {
      ...metadata(),
      members: [member(0)],
    });
    p.healthy = true;
    expect(p.visibleMembers(guild)?.[0].joinedAt).toBeNull();
    p.update(guild, person(1).id, voice, member(1), 100);
    p.update(guild, person(2).id, voice, member(2), 200);
    p.update(guild, person(1).id, voice, member(1), 300);
    expect(p.visibleMembers(guild)?.map((m) => m.userId)).toEqual(
      [2, 1, 0].map((n) => person(n).id),
    );
    p.update(guild, person(2).id, null);
    expect(p.visibleMembers(guild)?.map((m) => m.userId)).toEqual([1, 0].map((n) => person(n).id));
    p.invalidate();
    expect(p.visibleMembers(guild)).toBeNull();
  });
  it('excludes bots, unknown identities, restricted channels and other communities', () => {
    const p = new PresenceSnapshot();
    p.initialize(guild, [], metadata());
    p.healthy = true;
    p.update(guild, person(0).id, voice, member(0));
    p.update(guild, person(1).id, voice, member(1, true));
    p.update(guild, person(2).id, restricted, member(2));
    p.update(guild, person(3).id, voice);
    expect(p.visibleMembers(guild)?.map((m) => m.userId)).toEqual([person(0).id]);
    expect(p.visibleMembers('100000000000000999')).toBeNull();
    p.channel(guild, {
      id: voice,
      type: 2,
      permission_overwrites: [{ id: person(4).id, deny: '1024', allow: '0' }],
    });
    expect(p.visibleMembers(guild)).toEqual([]);
    p.channel(guild, { id: voice, type: 2, permission_overwrites: [] });
    p.role(guild, { id: guild, permissions: '0' });
    expect(p.visibleMembers(guild)).toEqual([]);
    p.channel(guild, {
      id: voice,
      type: 2,
      permission_overwrites: [{ id: guild, allow: '1024', deny: '0' }],
    });
    expect(p.visibleMembers(guild)).toHaveLength(1);
    p.channel(guild, { id: voice, type: 2 }, true);
    expect(p.members(guild)).toHaveLength(1); // Only the occupant of the other channel remains.
    p.remove(guild);
    expect(p.visibleMembers(guild)).toBeNull();
  });
});

describe('five-minute queue preview', () => {
  it('replaces the branding with at most five recent non-busy people, without pinging them', async () => {
    await start();
    await occupants([0, 1, 2, 3, 4, 5, 6, 7, 8]);
    await runInDurableObject(stub(), (_i, state) => {
      state.storage.sql.exec(
        'INSERT INTO active_players VALUES(?,?)',
        person(8).id,
        crypto.randomUUID(),
      );
    });
    await act('noadd', 99, { admin: true, target: person(7), seconds: 600 });
    await tick();
    const card = latestCard(),
      text = card.embeds[0].description.split('**In voice · not queued**')[1];
    expect(card.embeds[0].footer).toBeUndefined();
    expect(text.match(/<@\d+>/g)).toEqual([6, 5, 4, 3, 2].map((n) => `<@${person(n).id}>`));
    expect(card.allowed_mentions).toEqual({ parse: [] });
    expect((await stub().records(guild)).waiting[0].players).toHaveLength(1);
  });
  it('refreshes at five minutes and makes no Discord edits when the displayed list is unchanged', async () => {
    await start();
    await occupants([1]);
    await tick();
    vi.mocked(fetch).mockClear();
    await occupants([2]);
    await runDurableObjectAlarm(stub());
    await flush();
    expect(latestCard()).toBeUndefined();
    await tick();
    expect(latestCard().embeds[0].description).toContain(person(2).id);
    vi.mocked(fetch).mockClear();
    await tick();
    expect(latestCard()).toBeUndefined();
    await runInDurableObject(stub(), (_i, state) => {
      const cache = state.storage.kv.get<any>('queueVoice');
      expect(cache.due - cache.observedAt).toBe(300000);
    });
  });
  it('removes someone from the preview immediately when they join, and keeps its clock across restart', async () => {
    await start();
    await occupants([1, 2]);
    await tick();
    await abortAllDurableObjects();
    await act('add', 2);
    await flush();
    const text = latestCard().embeds[0].description.split('**In voice · not queued**')[1];
    expect(text).toContain(person(1).id);
    expect(text).not.toContain(person(2).id);
    await runInDurableObject(stub(), async (_i, state) => {
      expect(state.storage.kv.get<any>('queueVoice').due).toBeGreaterThan(Date.now());
      expect(await state.storage.getAlarm()).not.toBeNull();
    });
  });
  it('clears stale names when disconnected, distinguishes an empty list, and stops when paused', async () => {
    await start();
    await occupants([1]);
    await tick();
    await runInDurableObject(feed(), (i) => {
      (i as any).snapshot.invalidate();
    });
    await tick();
    expect(latestCard().embeds[0].description).toContain('temporarily unavailable');
    await occupants([]);
    await tick();
    expect(latestCard().embeds[0].description).toContain('No other players');
    const s = await stub().read(guild);
    s.settings.queues[0].enabled = false;
    await stub().save(guild, s.revision, s.settings, person(99).id, 'save');
    await flush();
    expect(latestCard().embeds[0].description).not.toContain('In voice');
    await runInDurableObject(stub(), async (_i, state) => {
      expect(state.storage.kv.get('queueVoice')).toBeUndefined();
      expect(await state.storage.getAlarm()).toBeNull();
    });
  });
  it('does not start a voice subscription for disabled previews or unpublished queues', async () => {
    const s = settings();
    s.queues[0].voicePreview = true;
    await setup(s);
    await runInDurableObject(stub(), async (_i, state) =>
      expect(await state.storage.getAlarm()).toBeNull(),
    );
    expect((await feed().status()).connected).toBe(false);
    expect(queueCard({ ...s.queues[0], voicePreview: false }, []).embeds[0]).not.toHaveProperty(
      'footer',
    );
  });
});

describe('bounded voice rollout and shared Gateway', () => {
  it('does not republish unchanged paused queues when activating one queue', async () => {
    const s = settings(),
      second = crypto.randomUUID();
    s.queues.push({
      ...structuredClone(s.queues[0]),
      id: second,
      name: 'Old demo',
      channelId: '706320010368516299',
    });
    await setup(s);
    await act('queue', 0);
    await act('queue', 0, { queue: 'Old demo', channelId: s.queues[1].channelId });
    await flush();
    const saved = await stub().read(guild);
    saved.settings.queues[1].enabled = false;
    await stub().save(guild, saved.revision, saved.settings, person(99).id, 'save');
    await flush();
    // Its saved card predates the renderer change in this release.
    await runInDurableObject(stub(), (_i, state) => {
      state.storage.sql.exec(
        'UPDATE queue_views SET digest=? WHERE queue_id=?',
        'old renderer',
        second,
      );
    });
    vi.mocked(fetch).mockClear();
    await stub().setVoicePreview(guild, 2, [queue], true, true);
    await flush();
    const writes = vi
      .mocked(fetch)
      .mock.calls.filter(([, init]) => ['POST', 'PATCH', 'DELETE'].includes(init?.method ?? ''));
    expect(writes.length).toBeGreaterThan(0);
    expect(writes.every(([url]) => !String(url).includes(s.queues[1].channelId))).toBe(true);
  });
  it('previews, applies and disables only the selected queue without changing players', async () => {
    await setup();
    await act('add', 0);
    await flush();
    const before = await stub().records(guild);
    expect(await stub().setVoicePreview(guild, 1, [queue], true, false)).toMatchObject({
      ok: true,
      applied: false,
    });
    expect((await stub().read(guild)).settings.queues[0].voicePreview).toBeUndefined();
    expect(await stub().setVoicePreview(guild, 1, [queue], true, true)).toMatchObject({
      ok: true,
      applied: true,
      revision: 2,
    });
    expect((await stub().records(guild)).waiting).toEqual(before.waiting);
    expect((await stub().setVoicePreview(guild, 1, [queue], false, true)).ok).toBe(false);
    expect((await stub().setVoicePreview(guild, 2, [crypto.randomUUID()], true, true)).ok).toBe(
      false,
    );
    expect(await stub().setVoicePreview(guild, 2, [queue], true, true)).toMatchObject({
      applied: false,
      revision: 2,
    });
    await stub().setVoicePreview(guild, 2, [queue], false, true);
    await runInDurableObject(stub(), (_i, state) =>
      expect(state.storage.kv.get('queueVoice')).toBeUndefined(),
    );
    await runInDurableObject(stub(), (_i, state) => state.storage.kv.put('recoveryReadOnly', true));
    expect((await stub().setVoicePreview(guild, 3, [queue], true, true)).ok).toBe(false);
  });
  it('requires operator authorization and rejects production or arbitrary settings', async () => {
    const url = `https://setup.example/ops/communities/${guild}/features/voice-preview`;
    expect((await exports.default.fetch(url, { method: 'POST', body: '{}' })).status).toBe(403);
    expect((await exports.default.fetch('https://setup.example/ops/voice')).status).toBe(403);
    const body = { expectedRevision: 1, queueIds: [queue], enabled: true, apply: true };
    const request = (value: object) =>
      new Request(url, {
        method: 'POST',
        headers: { Authorization: 'Bearer test-ops', 'Content-Type': 'application/json' },
        body: JSON.stringify(value),
      });
    expect(
      (await app.fetch(request(body), { ...env, APP_ENV: 'production', OPS_TOKEN: 'test-ops' }))
        .status,
    ).toBe(403);
    expect(
      (await app.fetch(request({ ...body, settings: {} }), { ...env, OPS_TOKEN: 'test-ops' }))
        .status,
    ).toBe(400);
  });
  it('shares one connection, keeps it alive beyond the handshake timeout, and stops after its lease', async () => {
    let upgrades = 0,
      aborted = false,
      intents = 0;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      if (String(input).includes('/gateway/bot'))
        return Response.json({
          url: 'wss://gateway.discord.gg',
          shards: 1,
          session_start_limit: { remaining: 100, reset_after: 60000 },
        });
      upgrades++;
      const pair = new WebSocketPair();
      pair[1].accept();
      init?.signal?.addEventListener('abort', () => {
        aborted = true;
      });
      pair[1].addEventListener('message', (event) => {
        const msg = JSON.parse(String(event.data));
        if (msg.op === 2) {
          intents = msg.d.intents;
          pair[1].send(
            JSON.stringify({
              op: 0,
              t: 'READY',
              s: 1,
              d: { session_id: 'synthetic', resume_gateway_url: 'wss://gateway.discord.gg' },
            }),
          );
          pair[1].send(
            JSON.stringify({
              op: 0,
              t: 'GUILD_CREATE',
              s: 2,
              d: {
                id: guild,
                ...metadata(),
                members: [member(1)],
                voice_states: [{ user_id: person(1).id, channel_id: voice }],
              },
            }),
          );
        } else if (msg.op === 1) pair[1].send(JSON.stringify({ op: 11, d: null }));
      });
      pair[1].send(JSON.stringify({ op: 10, d: { heartbeat_interval: 1000 } }));
      return new Response(null, { status: 101, webSocket: pair[0] });
    });
    await feed().query(guild, 'preview');
    await vi.waitFor(async () => expect((await feed().status()).healthy).toBe(true));
    expect((await feed().query(guild, 'preview')).preview?.[0].userId).toBe(person(1).id);
    await feed().query('100000000000000999', 'preview');
    expect(upgrades).toBe(1);
    expect(intents).toBe(129); // Guilds + Voice States; no message or member intent.
    await new Promise((resolve) => setTimeout(resolve, 5200));
    expect(aborted).toBe(false);
    expect((await feed().status()).healthy).toBe(true);
    await runInDurableObject(feed(), (instance) => {
      (instance as any).wantedUntil = 0;
    });
    await runDurableObjectAlarm(feed());
    expect(await feed().status()).toMatchObject({
      connected: false,
      healthy: false,
      idleStopAt: 0,
    });
  }, 10000);
  it('keeps the requested connection lease across an object restart', async () => {
    await occupants([1]);
    const before = await feed().query(guild, 'preview');
    await abortAllDurableObjects();
    expect((await feed().status()).idleStopAt).toBe(before.idleStopAt);
    expect((await feed().status()).healthy).toBe(false);
  });
});

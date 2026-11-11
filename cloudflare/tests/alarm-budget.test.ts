import { abortAllDurableObjects, runDurableObjectAlarm, runInDurableObject } from 'cloudflare:test';
import { describe, expect, it, vi } from 'vitest';
import { env } from 'cloudflare:workers';
import { act, channel, current, guild, harness, person, settings, setup, stub } from './harness';

harness();

const hour = 3600000,
  second = '706320010368516201',
  secondQueue = 'c7b1f1f4-3c55-4a43-9a43-6c0f0a3b2d11';

// A Durable Object alarm runs on a hibernating object: the test owns the clock and plays
// the alarm manager, waking a fresh instance at each armed time. An alarm armed during a
// handler for a time that is already due fires a second, overlapping invocation in
// production; such re-arms are counted as `twins`.
function simulator() {
  let clock = Date.now() + 86400000,
    armed: number | null = null,
    inAlarm = false,
    calls: (number | null)[] = [];
  vi.spyOn(Date, 'now').mockImplementation(() => clock);
  // Discord keeps each channel's messages newest first; `refused` channels answer 403.
  const requests: string[] = [],
    messages = new Map<string, string[]>(),
    refused = new Set<string>();
  let sequence = 100000000007000000n;
  const normal = vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    const r = new Request(input, init),
      url = new URL(r.url);
    if (url.hostname === 'discord.com') requests.push(`${r.method} ${url.pathname}`);
    const route = /\/channels\/(\d+)\/messages(?:\/(\d+))?$/.exec(url.pathname);
    if (!route) return normal(input, init);
    const [, id, message] = route;
    if (refused.has(id)) return Response.json({ code: 50001 }, { status: 403 });
    const list = messages.get(id) ?? [];
    messages.set(id, list);
    if (r.method === 'GET')
      return Response.json(
        list
          .slice(0, Number(url.searchParams.get('limit')))
          .map((m) => ({ id: m, author: { id: '100000000000000001' } })),
      );
    if (r.method === 'POST') {
      list.unshift(String(sequence++));
      return Response.json({ id: list[0] });
    }
    const index = list.indexOf(message);
    if (index < 0) return new Response(null, { status: 404 });
    if (r.method === 'DELETE') {
      list.splice(index, 1);
      return new Response(null, { status: 204 });
    }
    return Response.json({ id: message });
  });
  let proto: any,
    restore = () => {};
  const hook = async () => {
    if (proto) return;
    proto = await runInDurableObject(stub(), (_i, state) => Object.getPrototypeOf(state.storage));
    const set = proto.setAlarm,
      del = proto.deleteAlarm;
    proto.setAlarm = function (t: number | Date, ...rest: unknown[]) {
      if (inAlarm) calls.push(Number(t));
      return set.call(this, t, ...rest);
    };
    proto.deleteAlarm = function (...rest: unknown[]) {
      if (inAlarm) calls.push(null);
      return del.apply(this, rest);
    };
    restore = () => Object.assign(proto, { setAlarm: set, deleteAlarm: del });
  };
  // Reads the armed time and the earliest timed work without waking a fresh instance.
  const inspect = () =>
    runInDurableObject(stub(), async (_i, state) => ({
      armed: await state.storage.getAlarm(),
      due: state.storage.sql
        .exec<{ due: number | null }>(
          "SELECT MIN(due) AS due FROM (SELECT due FROM deadlines UNION ALL SELECT due FROM queue_clocks UNION ALL SELECT due FROM outbox WHERE status='pending')",
        )
        .toArray()[0].due,
    }));
  const stats = { invocations: 0, twins: 0, late: 0 };
  return {
    stats,
    requests,
    refused,
    now: () => clock,
    restore: () => restore(),
    // Runs every alarm due up to `until`, evicting the object before each one.
    async run(until: number) {
      await hook();
      const fired: number[] = [];
      let { armed } = await inspect();
      for (let guard = 0; guard < 5000 && armed !== null && armed <= until; guard++) {
        clock = Math.max(clock, armed);
        fired.push(clock);
        await abortAllDurableObjects();
        calls = [];
        inAlarm = true;
        try {
          await runDurableObjectAlarm(stub());
        } finally {
          inAlarm = false;
        }
        stats.invocations++;
        if (calls.slice(0, -1).some((at) => at !== null && at <= clock + 50)) stats.twins++;
        const after = await inspect();
        // Timed work must keep an alarm no later than its due time.
        if (
          after.due !== null &&
          (after.armed === null || after.armed > Math.max(after.due, clock + 50))
        )
          stats.late++;
        armed = after.armed;
      }
      clock = Math.max(clock, until);
      return fired;
    },
  };
}

function twoQueues() {
  const s = settings();
  s.queues[0].autoStart = false;
  s.queues.push({
    ...structuredClone(s.queues[0]),
    id: secondQueue,
    name: 'Evening',
    channelId: second,
  });
  return s;
}

describe('alarm budget', () => {
  it('re-arms each alarm once, for a time that is not already due', async () => {
    const sim = simulator();
    try {
      await setup(twoQueues());
      await act('add', 0);
      await sim.run(sim.now() + hour);
      console.log(JSON.stringify({ firstHour: sim.stats }));
      expect(sim.stats.invocations).toBeGreaterThan(0);
      expect(sim.stats.twins).toBe(0);
    } finally {
      sim.restore();
    }
  }, 120000);

  it('keeps an idle day with two enabled queues within a small alarm and Discord budget', async () => {
    const sim = simulator();
    try {
      await setup(twoQueues());
      await act('add', 0);
      await act('remove', 0);
      // Deliver the cards from that activity, then measure the following idle day.
      await sim.run(sim.now() + 60000);
      const start = sim.now();
      sim.stats.invocations = 0;
      sim.requests.length = 0;
      await sim.run(start + 24 * hour);
      const day = {
        invocations: sim.stats.invocations,
        twins: sim.stats.twins,
        requests: sim.requests.length,
      };
      console.log(JSON.stringify({ idleDay: day }));
      // 6 hours of 15-minute checks after the last activity, one history read per channel.
      expect(day.twins).toBe(0);
      expect(sim.stats.late).toBe(0);
      expect(day.invocations).toBeLessThanOrEqual(26);
      expect(day.requests).toBeLessThanOrEqual(50);
      sim.stats.invocations = 0;
      sim.requests.length = 0;
      await sim.run(sim.now() + 24 * hour);
      console.log(
        JSON.stringify({
          secondIdleDay: { invocations: sim.stats.invocations, requests: sim.requests.length },
        }),
      );
      expect(sim.stats.invocations).toBeLessThanOrEqual(2);
      expect(sim.requests).toEqual([]);
    } finally {
      sim.restore();
    }
  }, 120000);

  it('fires check-in reminders, deadlines and waiting expiry on time during an active evening', async () => {
    const sim = simulator();
    try {
      const s = twoQueues();
      s.queues[0].autoStart = true;
      s.queues[0].checkIn = { enabled: true, seconds: 120 };
      s.queues[1].controls = {
        waitingSeconds: 1800,
        matchSeconds: 0,
        requiredRole: null,
        blockedRole: null,
      };
      await setup(s);
      const evening = sim.now();
      // Someone waits in the second queue; their place expires after 30 minutes.
      await stub().play(guild, {
        id: '100000000000900001',
        actor: person(20),
        kind: 'add',
        channelId: second,
        admin: false,
      });
      const timers = async () =>
        runInDurableObject(stub(), (_i, state) =>
          state.storage.sql
            .exec<{ kind: string; due: number }>(
              "SELECT kind,due FROM deadlines UNION ALL SELECT 'waiting',due FROM queue_clocks",
            )
            .toArray(),
        );
      for (let n = 0; n < 4; n++) await act('add', n);
      const m = await current();
      expect(m.state).toBe('check_in');
      await act('ready', 0, { matchId: m.id });
      const expected = await timers();
      expect(expected.map((t) => t.kind)).toEqual(expect.arrayContaining(['check_in', 'waiting']));
      const end = evening + 3 * hour;
      const fired = await sim.run(end);
      console.log(JSON.stringify({ evening: { timers: expected, invocations: fired.length } }));
      // Each timer fired at its due time, not later, and no invocation was doubled.
      for (const t of expected.filter((t) => t.due <= end)) expect(fired).toContain(t.due);
      expect(sim.stats).toMatchObject({ twins: 0, late: 0 });
      expect((await current()).state).not.toBe('check_in');
      const waiting = (await stub().records(guild)).waiting.find((w) => w.queueId === secondQueue)!;
      expect(waiting.players).toEqual([]);
    } finally {
      sim.restore();
    }
  }, 120000);

  it('checks queue cards only while a queue is in use, and resumes on the next join', async () => {
    const sim = simulator();
    try {
      await setup(twoQueues());
      await act('add', 0);
      await sim.run(sim.now() + 7 * hour);
      const checks = () => sim.requests.filter((r) => r.startsWith('GET ')).length;
      expect(checks()).toBeGreaterThan(0);
      const idle = await runInDurableObject(stub(), async (_i, state) => ({
        due: state.storage.kv.get('queueVisibilityDue'),
        alarm: await state.storage.getAlarm(),
      }));
      expect(idle.due).toBeUndefined();
      sim.requests.length = 0;
      await act('add', 1);
      await sim.run(sim.now() + 16 * 60000);
      // The join publishes the card in place; the periodic check runs again 15 minutes later.
      expect(
        sim.requests.filter((r) => r === `GET /api/v10/channels/${channel}/messages`),
      ).toHaveLength(2);
      expect(sim.requests.some((r) => r.includes(second))).toBe(false);
    } finally {
      sim.restore();
    }
  }, 120000);

  it('keeps the visibility switch meaningful while a queue is in use', async () => {
    const sim = simulator();
    try {
      const s = twoQueues();
      for (const q of s.queues) q.keepQueueVisible = false;
      await setup(s);
      await act('add', 0);
      await sim.run(sim.now() + 60000);
      sim.requests.length = 0;
      await sim.run(sim.now() + 2 * hour);
      expect(sim.requests).toEqual([]);
    } finally {
      sim.restore();
    }
  }, 120000);

  it('stops polling a channel Discord keeps refusing until a save retries it', async () => {
    const sim = simulator();
    try {
      await setup(twoQueues());
      await act('add', 0);
      await stub().play(guild, {
        id: '100000000000900002',
        actor: person(20),
        kind: 'add',
        channelId: second,
        admin: false,
      });
      await sim.run(sim.now() + 60000);
      sim.refused.add(channel);
      sim.requests.length = 0;
      await sim.run(sim.now() + 3 * hour);
      const refused = sim.requests.filter((r) => r.includes(`/channels/${channel}/`));
      // Five refused checks over an hour, then none; the other channel is still checked.
      expect(refused).toHaveLength(5);
      expect(sim.requests.filter((r) => r.includes(`/channels/${second}/`)).length).toBeGreaterThan(
        5,
      );
      const records = await stub().records(guild);
      expect(records.unreachableChannels).toMatchObject([
        { channelId: channel, queueIds: [settings().queues[0].id] },
      ]);
      expect(records.delivery[0]).toMatchObject({
        key: `channel:${channel}`,
        status: 'blocked',
        error: `The bot can't reach #${channel} (Pickup). Re-add the bot or fix its permissions, then save the queue.`,
      });
      // Timed deliveries to that channel are held without calling Discord.
      await runInDurableObject(stub(), (_i, state) => {
        state.storage.sql.exec(
          "INSERT INTO cards(key,channel_id,nonce) VALUES('note:1',?,'n1')",
          channel,
        );
        state.storage.sql.exec(
          "INSERT INTO outbox(key,payload,version,due) VALUES('note:1',?,1,0)",
          JSON.stringify({
            content: 'x',
            embeds: [],
            components: [],
            allowed_mentions: { parse: [] },
          }),
        );
        state.storage.setAlarm(Date.now());
      });
      sim.requests.length = 0;
      await sim.run(sim.now() + 60000);
      expect(sim.requests.filter((r) => r.includes(`/channels/${channel}/`))).toEqual([]);
      const held = () =>
        runInDurableObject(
          stub(),
          (_i, state) =>
            state.storage.sql
              .exec("SELECT status,error FROM outbox WHERE key='note:1'")
              .toArray()[0],
        );
      expect(await held()).toEqual({ status: 'blocked', error: 'channel_unreachable' });
      // The organizer fixes access and saves: the state clears and held work is retried.
      sim.refused.delete(channel);
      const saved = await stub().read(guild);
      await stub().save(guild, saved.revision, saved.settings, person(99).id, 'save');
      expect((await stub().records(guild)).unreachableChannels).toEqual([]);
      expect(await held()).toMatchObject({ status: 'pending' });
      await sim.run(sim.now() + 60000);
      expect(
        sim.requests.filter((r) => r === `POST /api/v10/channels/${channel}/messages`),
      ).toHaveLength(1);
    } finally {
      sim.restore();
    }
  }, 120000);

  it('holds the voice Gateway lease only while a voice-preview queue is in use', async () => {
    const sim = simulator();
    try {
      const s = twoQueues();
      s.queues[0].voicePreview = true;
      await setup(s);
      await act('add', 0);
      await act('remove', 0);
      const leases = () => sim.requests.filter((r) => r === 'GET /api/v10/gateway/bot').length;
      await sim.run(sim.now() + hour);
      expect(leases()).toBeGreaterThan(5);
      await sim.run(sim.now() + 6 * hour);
      sim.requests.length = 0;
      await sim.run(sim.now() + 6 * hour);
      expect(leases()).toBe(0);
      const feed = () => env.VOICE_PRESENCE.getByName(env.DISCORD_APPLICATION_ID!);
      expect((await feed().status()).idleStopAt).toBeLessThan(sim.now());
      expect(
        await runInDurableObject(stub(), (_i, state) => state.storage.kv.get('queueVoice')),
      ).toBeUndefined();
      await act('add', 1);
      await sim.run(sim.now() + 60000);
      expect(leases()).toBe(1);
      expect((await feed().status()).idleStopAt).toBeGreaterThan(sim.now());
    } finally {
      sim.restore();
    }
  }, 120000);
});

import { env } from 'cloudflare:workers';
import { interactions } from '../src/discord/interactions';
import { matchCard, queueCard } from '../src/discord/cards';
import { reset, runInDurableObject } from 'cloudflare:test';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { emptyModules, type Settings } from '../src/domain/config';
import type { Action, Match } from '../src/domain/play';
import { queuePicker } from '../src/discord/queue-picker';

const guild = '706320010368516198',
  channel = '706320010368516200';
const app = '100000000000000001';
const queue = '81da571b-6a4a-4e5d-8e32-f4050df35a2d';
const otherQueue = '81da571b-6a4a-4e5d-8e32-f4050df35a2e';
const game = '4daebc0a-55e8-4d56-a17d-d94913644910';
const person = (n: number) => ({
  id: String(100000000000000100n + BigInt(n)),
  name: `Player ${n}`,
});
const stub = () => env.COMMUNITIES.getByName(guild);
let seq = 100000000000001000n;
const settings = (): Settings => ({
  games: [{ id: game, name: 'RTS', catalogId: null }],
  queues: [
    {
      id: queue,
      gameId: game,
      name: 'Pickup',
      channelId: channel,
      size: 4,
      enabled: true,
      checkIn: { enabled: true, seconds: 60 },
      teamMethod: 'balanced',
      ratingEngine: 'aoe2',
      modules: emptyModules,
    },
  ],
});
const act = (kind: Action['kind'], n = 0, rest: Partial<Action> = {}) =>
  stub().play(guild, {
    id: String(seq++),
    actor: person(n),
    channelId: channel,
    admin: false,
    kind,
    ...rest,
  });
async function configure(s = settings()) {
  await stub().save(guild, 0, s, person(99).id, 'save');
}
async function current(): Promise<Match> {
  return (await stub().records(guild)).matches[0] as Match;
}
async function fill(playing = false) {
  const s = settings();
  if (playing) s.queues[0].checkIn.enabled = false;
  await configure(s);
  for (let n = 0; n < 4; n++) await act('add', n);
  return current();
}
async function interaction(data: object, n = 0, rest: object = {}) {
  const raw = JSON.stringify({
    id: String(seq++),
    type: 3,
    application_id: app,
    token: 'synthetic-interaction-token',
    guild_id: guild,
    channel_id: channel,
    member: { permissions: '0', user: { id: person(n).id, username: person(n).name } },
    data,
    ...rest,
  });
  const timestamp = String(Math.floor(Date.now() / 1000));
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
  const sig = new Uint8Array(
    await crypto.subtle.sign('Ed25519', key, new TextEncoder().encode(timestamp + raw)),
  );
  const jobs: Promise<unknown>[] = [];
  const response = await interactions(
    new Request('https://setup.example/interactions', {
      method: 'POST',
      body: raw,
      headers: {
        'X-Signature-Timestamp': timestamp,
        'X-Signature-Ed25519': Array.from(sig, (b) => b.toString(16).padStart(2, '0')).join(''),
      },
    }),
    env,
    { waitUntil: (job) => jobs.push(job) },
  );
  for (let completed = 0; completed < jobs.length;) {
    const batch = jobs.slice(completed);
    completed = jobs.length;
    await Promise.all(batch);
  }
  expect(response.status).toBe(200);
  return response.json() as Promise<{
    type: number;
    data: { content: string; flags: number; components?: any[]; embeds?: any[] };
  }>;
}
const click = (m: Match, kind: string, n = 0, values?: string[]) =>
  interaction({ custom_id: `m:${m.id}:${m.version}:${kind}`, ...(values ? { values } : {}) }, n);

function adminCommand(
  group: string,
  action: string,
  args: Record<string, unknown> = {},
  permissions = '32',
  rest: object = {},
) {
  const people = [...Array.from({ length: 10 }, (_, n) => person(n)), person(99)];
  return interaction(
    {
      name: 'manage',
      options: [
        {
          type: 2,
          name: group,
          options: [
            {
              type: 1,
              name: action,
              options: Object.entries(args).map(([name, value]) => ({ name, value })),
            },
          ],
        },
      ],
      resolved: {
        users: Object.fromEntries(people.map((p) => [p.id, { id: p.id, username: p.name }])),
        members: Object.fromEntries(people.map((p) => [p.id, { roles: [] }])),
      },
    },
    99,
    {
      type: 2,
      member: { permissions, user: { id: person(99).id, username: person(99).name } },
      ...rest,
    },
  );
}

beforeEach(() => {
  seq = 100000000000001000n;
  vi.spyOn(globalThis, 'fetch').mockImplementation(async () =>
    Response.json({ id: '100000000000008888' }),
  );
});
afterEach(async () => {
  await reset();
  vi.restoreAllMocks();
});

describe('private administrator panel', () => {
  const role = '100000000000000777';
  const identity = (permissions = '32', roles: string[] = [], n = 99) => ({
    permissions,
    roles,
    user: { id: person(n).id, username: person(n).name },
  });
  async function panel(data: object = { name: 'admin' }, rest: object = {}) {
    const start = vi.mocked(fetch).mock.calls.length;
    const response = await interaction(data, 99, { type: 2, member: identity(), ...rest });
    const patch = vi
      .mocked(fetch)
      .mock.calls.slice(start)
      .filter(([url, init]) => String(url).includes('/webhooks/') && init?.method === 'PATCH')
      .at(-1);
    return { response, card: patch ? JSON.parse(patch[1]!.body as string) : response.data };
  }
  const control = (card: any, label: string) =>
    card.components.flatMap((row: any) => row.components).find((c: any) => c.label === label);
  const press = (card: any, label: string, rest: object = {}) =>
    panel(
      { custom_id: control(card, label).custom_id },
      {
        type: 3,
        message: { flags: 64, author: { id: app } },
        ...rest,
      },
    );
  const bodyText = (card: any) => JSON.stringify(card.embeds);

  it.each(['8', '32'])(
    'opens a private status panel for Discord permission %s',
    async (permissions) => {
      await configure();
      await act('add', 0);
      const { response, card } = await panel({ name: 'admin' }, { member: identity(permissions) });
      expect(response).toEqual({ type: 5, data: { flags: 64 } });
      expect(bodyText(card)).toContain('1/4 waiting');
      expect(bodyText(card)).toContain('No matches yet');
      expect(control(card, 'Start queue').disabled).toBe(true);
      expect(control(card, 'Settings').url).toContain(`community=${guild}`);
      expect(card.allowed_mentions).toEqual({ parse: [] });
    },
  );

  it('denies a regular member even when Discord command visibility was overridden', async () => {
    await configure();
    const { card } = await panel({ name: 'admin' }, { member: identity('0') });
    expect(card.content).toContain('configured queue admin role');
    expect(card.components).toEqual([]);
    expect(card.embeds).toEqual([]);
  });

  it('allows a configured role only for its queue, without website or /manage authority', async () => {
    const s = settings();
    s.queues[0].adminRoles = [role];
    s.queues.push({
      ...structuredClone(s.queues[0]),
      id: otherQueue,
      name: 'Private queue',
      adminRoles: [],
    });
    await configure(s);
    const allowed = await panel({ name: 'admin' }, { member: identity('0', [role]) });
    expect(bodyText(allowed.card)).toContain('Pickup');
    expect(control(allowed.card, 'Settings')).toBeUndefined();
    expect(JSON.stringify(allowed.card)).not.toContain('Private queue');
    const denied = await panel(
      { name: 'admin', options: [{ name: 'queue', value: otherQueue }] },
      { member: identity('0', [role]) },
    );
    expect(denied.card.content).toContain('you administer');
    expect(
      (await adminCommand('setup', 'status', {}, '0', { member: identity('0', [role]) })).data
        .content,
    ).toContain('Manage Server');
  });

  it.each(['member', 'configuration'])(
    'rechecks %s role removal before a button action',
    async (removal) => {
      const s = settings();
      s.queues[0].adminRoles = [role];
      await configure(s);
      await act('add', 0);
      const { card } = await panel({ name: 'admin' }, { member: identity('0', [role]) });
      if (removal === 'configuration') {
        const saved = await stub().read(guild);
        saved.settings.queues[0].adminRoles = [];
        await stub().save(guild, saved.revision, saved.settings, person(99).id, 'save');
      }
      const denied = await press(card, 'Clear queue', {
        member: identity('0', removal === 'member' ? [] : [role]),
      });
      expect(denied.card.content).toContain('configured queue admin role');
      expect((await stub().records(guild)).waiting[0].players).toHaveLength(1);
    },
  );

  it.each(['owner', 'public', 'author', 'channel', 'guild'])(
    'rejects a panel with the wrong %s',
    async (wrong) => {
      await configure();
      await act('add', 0);
      const { card } = await panel();
      const rest =
        wrong === 'owner'
          ? { member: identity('32', [], 98) }
          : wrong === 'public'
            ? { message: { flags: 0, author: { id: app } } }
            : wrong === 'author'
              ? { message: { flags: 64, author: { id: person(0).id } } }
              : wrong === 'channel'
                ? { channel_id: '100000000000000888' }
                : { guild_id: '100000000000000888' };
      const denied = await press(card, 'Clear queue', rest);
      expect(denied.card.content).toMatch(/panel|role/);
      expect((await stub().records(guild)).waiting[0].players).toHaveLength(1);
    },
  );

  it('starts a queued roster with the saved check-in rules and rejects old start buttons', async () => {
    const s = settings();
    s.queues[0].autoStart = false;
    await configure(s);
    for (let n = 0; n < 4; n++) await act('add', n);
    const { card } = await panel();
    expect(control(card, 'Start queue').disabled).toBe(false);
    const started = await press(card, 'Start queue');
    const match = await current();
    expect(match.state).toBe('check_in');
    expect(bodyText(started.card)).toContain(match.id);
    expect(control(started.card, 'Alpha lost').disabled).toBe(true);
    expect((await press(card, 'Start queue')).card.content).toContain('queue changed');
    expect((await stub().records(guild)).matches).toHaveLength(1);
  });

  it('confirms a result, updates the same private panel, and cannot settle twice', async () => {
    const m = await fill(true);
    const { card } = await panel();
    expect(bodyText(card)).toContain(m.id);
    const prompt = await press(card, 'Alpha lost');
    expect(prompt.response.type).toBe(6);
    expect(bodyText(prompt.card)).toContain('Beta wins');
    expect((await current()).state).toBe('playing');
    const completed = await press(prompt.card, 'Confirm');
    expect((await current()).result).toBe('beta');
    expect(bodyText(completed.card)).toContain('Beta won');
    expect(control(completed.card, 'Alpha lost')).toBeUndefined();
    const before = await stub().operationalBackup(guild);
    expect((await press(prompt.card, 'Confirm')).card.content).toMatch(/changed|ended/);
    const after = await stub().operationalBackup(guild);
    for (const table of ['matches', 'profiles', 'rating_changes', 'wallets', 'gold_ledger'])
      expect(after.tables[table]).toEqual(before.tables[table]);
    expect(
      vi
        .mocked(fetch)
        .mock.calls.some(
          ([url, init]) => String(url).includes('/webhooks/') && init?.method === 'DELETE',
        ),
    ).toBe(false);
  });

  it('requires confirmation to clear waiting players and rejects a changed queue', async () => {
    await configure();
    await act('add', 0);
    const { card } = await panel();
    const prompt = await press(card, 'Clear queue');
    expect((await stub().records(guild)).waiting[0].players).toHaveLength(1);
    await act('add', 1);
    expect((await press(prompt.card, 'Confirm')).card.content).toContain('queue changed');
    const fresh = await panel();
    const clear = await press(fresh.card, 'Clear queue');
    await press(clear.card, 'Confirm');
    expect((await stub().records(guild)).waiting[0].players).toEqual([]);
  });

  it('cancels an ongoing match through a confirmation without rating it', async () => {
    const m = await fill();
    const { card } = await panel();
    const prompt = await press(card, 'Cancel match');
    expect((await current()).state).toBe('check_in');
    await press(prompt.card, 'Confirm');
    expect((await current()).state).toBe('aborted');
    expect((await current()).changes).toEqual([]);
    expect(bodyText((await panel()).card)).toContain(m.id);
  });

  it.each(['Beta lost', 'Draw'])('records %s with the existing settlement rules', async (label) => {
    const s = settings();
    s.queues[0].checkIn.enabled = false;
    s.queues[0].modules.gold = true;
    s.queues[0].modules.predictions = true;
    await configure(s);
    for (let n = 0; n < 4; n++) await act('add', n);
    const { card } = await panel();
    const close = await press(card, 'Close betting');
    await press(close.card, 'Confirm');
    const fresh = await panel();
    const prompt = await press(fresh.card, label);
    await press(prompt.card, 'Confirm');
    expect((await current()).result).toBe(label === 'Draw' ? 'draw' : 'alpha');
    const backup = await stub().operationalBackup(guild);
    expect(backup.tables.books.every((b) => b.status !== 'open')).toBe(true);
    await runInDurableObject(stub(), (_i, state) =>
      expect(
        state.storage.sql
          .exec(
            'SELECT w.user_id FROM wallets w LEFT JOIN gold_ledger l ON l.game_id=w.game_id AND l.user_id=w.user_id GROUP BY w.game_id,w.user_id HAVING w.balance!=COALESCE(SUM(l.amount),0)',
          )
          .toArray(),
      ).toEqual([]),
    );
  });

  it('selects another ongoing match explicitly and keeps refresh controls within Discord limits', async () => {
    const s = settings();
    s.queues[0].checkIn.enabled = false;
    s.queues.push({ ...structuredClone(s.queues[0]), id: otherQueue, name: 'Second' });
    await configure(s);
    for (let n = 0; n < 8; n++) await act('add', n, { queue: 'Pickup' });
    const matches = (await stub().records(guild)).matches as Match[];
    const { card } = await panel({ name: 'admin', options: [{ name: 'queue', value: 'Pickup' }] });
    expect(card.components).toHaveLength(5);
    const menu = card.components
      .flatMap((r: any) => r.components)
      .find((c: any) => c.custom_id?.endsWith(':selectm'));
    const selected = await panel(
      { custom_id: menu.custom_id, values: [matches[1].id] },
      { type: 3, message: { flags: 64, author: { id: app } } },
    );
    expect(
      selected.card.embeds.find((e: any) => e.title === 'Selected match').description,
    ).toContain(matches[1].id);
    const refreshed = await press(selected.card, 'Refresh match card');
    expect(refreshed.response.type).toBe(6);
    expect((await stub().records(guild)).matches).toHaveLength(2);
    expect(
      card.components
        .flatMap((r: any) => r.components)
        .every((c: any) => !c.custom_id || c.custom_id.length <= 100),
    ).toBe(true);
  });

  it('rechecks queue admin grants after asynchronous start eligibility checks', async () => {
    const s = settings();
    s.queues[0].autoStart = false;
    s.queues[0].adminRoles = [role];
    await configure(s);
    for (let n = 0; n < 4; n++) await act('add', n);
    const saved = await stub().read(guild);
    saved.settings.queues[0].controls = {
      requiredRole: role,
      blockedRole: null,
      waitingSeconds: 0,
      matchSeconds: 0,
    };
    await stub().save(guild, saved.revision, saved.settings, person(99).id, 'save');
    const { card } = await panel({ name: 'admin' }, { member: identity('0', [role]) });
    const normal = vi.mocked(fetch).getMockImplementation()!;
    let revoked: Promise<unknown> | undefined;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const member = /\/members\/(\d+)$/.exec(new Request(input, init).url);
      if (!member) return normal(input, init);
      revoked ??= (async () => {
        const now = await stub().read(guild);
        now.settings.queues[0].adminRoles = [];
        return stub().save(guild, now.revision, now.settings, person(99).id, 'save');
      })();
      await revoked;
      return Response.json({ user: { id: member[1] }, roles: [role] });
    });
    const rejected = await press(card, 'Start queue', { member: identity('0', [role]) });
    expect(revoked).toBeDefined();
    expect(rejected.card.content).toContain('configured queue admin role');
    expect((await stub().records(guild)).matches).toEqual([]);
    expect((await stub().records(guild)).waiting[0].players).toHaveLength(4);
  });

  it('shows the last five results for the selected queue even with newer matches elsewhere', async () => {
    const m = await fill(true);
    const own: string[] = [];
    await runInDurableObject(stub(), (_i, state) => {
      for (let n = 0; n < 32; n++) {
        const next = structuredClone(m);
        next.id = crypto.randomUUID();
        next.createdAt += n + 1;
        next.state = 'finished';
        next.result = n % 2 ? 'alpha' : 'beta';
        next.closedAt = next.createdAt + 1;
        if (n >= 7) next.queue.id = otherQueue;
        else own.push(next.id);
        state.storage.sql.exec(
          'INSERT INTO matches VALUES(?,?,?,?,?,?,?)',
          next.id,
          game,
          next.queue.id,
          channel,
          next.state,
          next.createdAt,
          JSON.stringify(next),
        );
      }
    });
    const { card } = await panel();
    const recent = card.embeds.find((e: any) => e.title === 'Last 5 completed matches').description;
    expect(own.slice(2).every((id) => recent.includes(id))).toBe(true);
    expect(own.slice(0, 2).some((id) => recent.includes(id))).toBe(false);
    expect(card.embeds[0].description).toContain(own.at(-1));
    expect(card.embeds[1].description).toContain(m.id); // Older ongoing match remains actionable.
    expect(card.components.length).toBeLessThanOrEqual(5);
    expect(
      card.components
        .flatMap((r: any) => r.components)
        .every((c: any) => !c.custom_id || c.custom_id.length <= 100),
    ).toBe(true);
    expect(
      card.embeds.reduce((n: number, e: any) => n + e.title.length + e.description.length, 0),
    ).toBeLessThanOrEqual(6000);
  });

  it('refuses @everyone as an admin role and keeps recovery copies read-only', async () => {
    const s = settings();
    s.queues[0].adminRoles = [guild];
    expect((await stub().save(guild, 0, s, person(99).id, 'save')).ok).toBe(false);
    expect((await stub().read(guild)).settings.queues).toEqual([]);
    await configure();
    await runInDurableObject(stub(), (_i, state) => state.storage.kv.put('recoveryReadOnly', true));
    expect((await panel()).card.content).toContain('read-only');
  });
});

describe('categorized administrator commands', () => {
  it.each(['setup', 'queue', 'match', 'player'])(
    'denies ordinary members before dispatching the %s category',
    async (group) => {
      await configure();
      const before = await stub().records(guild);
      const reply = await adminCommand(group, 'anything', {}, '0');
      expect(reply.data.flags).toBe(64);
      expect(reply.data.content).toContain('Manage Server permission');
      expect(await stub().records(guild)).toEqual(before);
    },
  );

  it('opens setup and reports status from the grouped commands', async () => {
    await configure();
    expect((await adminCommand('setup', 'status')).data.content).toContain('settings revision 1');
    expect((await adminCommand('setup', 'web')).data.content).toContain(`/?community=${guild}`);
  });

  it('adds players, seeds their automatically created profile, and removes them', async () => {
    await configure();
    await adminCommand('queue', 'add', { user: person(0).id });
    let records = await stub().records(guild);
    expect(records.waiting[0].players.map((p) => p.id)).toEqual([person(0).id]);
    expect(records.players.find((p) => p.id === person(0).id)?.rating).toBe(1000);
    await adminCommand('player', 'seed', { user: person(0).id, rating: 1600 });
    records = await stub().records(guild);
    expect(records.players.find((p) => p.id === person(0).id)?.rating).toBe(1600);
    await adminCommand('queue', 'remove', { user: person(0).id });
    expect((await stub().records(guild)).waiting[0].players).toHaveLength(0);
  });

  it('preserves the manual-start skip-check-in option', async () => {
    const s = settings();
    s.queues[0].autoStart = false;
    await configure(s);
    for (let n = 0; n < 4; n++) await act('add', n);
    await adminCommand('queue', 'start', { skip_check_in: true });
    expect((await current()).state).toBe('playing');
  });

  it('requires an explicit match before cancelling, and rejects a different channel', async () => {
    const m = await fill(true);
    expect((await adminCommand('match', 'cancel')).data.content).toContain('required options');
    expect((await current()).state).toBe('playing');
    await adminCommand('match', 'cancel', { match: m.id }, '32', {
      channel_id: '706320010368516201',
    });
    expect((await current()).state).toBe('playing');
    await adminCommand('match', 'cancel', { match: m.id });
    expect((await current()).state).toBe('aborted');
  });

  it('reports, undoes and corrects a result through the match category', async () => {
    const m = await fill(true);
    await adminCommand('match', 'report', { match: m.id, result: 'alpha' });
    expect(await current()).toMatchObject({ state: 'finished', result: 'alpha' });
    await adminCommand('match', 'undo', { match: m.id });
    expect((await current()).state).toBe('undone');
    await adminCommand('match', 'report', { match: m.id, result: 'beta' });
    expect(await current()).toMatchObject({ state: 'finished', result: 'beta' });
    expect((await current()).changes).toHaveLength(4);
  });

  it('restricts queue access and removes the restriction through the player category', async () => {
    await configure();
    await adminCommand('player', 'restrict', { user: person(0).id, seconds: 60 });
    expect((await act('add', 0)).ok).toBe(false);
    await adminCommand('player', 'unrestrict', { user: person(0).id });
    expect((await act('add', 0)).content).toContain('Joined Pickup');
  });

  it('rejects unknown categories and missing required users without changing data', async () => {
    await configure();
    const before = await stub().records(guild);
    expect((await adminCommand('__proto__', 'seed')).data.content).toContain('Choose an action');
    expect((await adminCommand('queue', 'add')).data.content).toContain('required options');
    expect((await adminCommand('player', 'seed', { user: person(0).id })).data.content).toContain(
      'required options',
    );
    expect(await stub().records(guild)).toEqual(before);
  });
});

describe('player component routes', () => {
  it('removes slash add/remove thinking responses while persisting the public queue changes', async () => {
    await configure();
    for (const name of ['add', 'remove']) {
      vi.mocked(fetch).mockClear();
      expect(await interaction({ name }, 0, { type: 2 })).toEqual({ type: 5, data: { flags: 64 } });
      const callbacks = vi
        .mocked(fetch)
        .mock.calls.filter(([url]) => String(url).includes('/webhooks/'));
      expect(callbacks).toHaveLength(1);
      expect(callbacks[0][1]?.method).toBe('DELETE');
      expect(callbacks[0][1]?.body).toBeUndefined();
      expect((await stub().records(guild)).waiting[0].players).toHaveLength(name === 'add' ? 1 : 0);
    }
  });
  it('silently checks in on the public card without deleting it through a webhook', async () => {
    const m = await fill();
    vi.mocked(fetch).mockClear();
    expect(
      await interaction({ custom_id: `m:${m.id}:${m.version}:ready` }, 0, {
        message: { flags: 0, author: { id: app } },
      }),
    ).toEqual({ type: 6 });
    expect((await current()).ready).toContain(person(0).id);
    expect(
      vi.mocked(fetch).mock.calls.filter(([url]) => String(url).includes('/webhooks/')),
    ).toEqual([]);
  });
  it('removes an owned private back-out panel only after the confirmed action succeeds', async () => {
    const m = await fill();
    const data = { custom_id: `m:${m.id}:${m.version}:confirm_back` },
      message = { flags: 64, author: { id: app } };
    vi.mocked(fetch).mockClear();
    expect((await interaction(data, 9, { message })).data.flags).toBe(64);
    expect(
      vi.mocked(fetch).mock.calls.filter(([url]) => String(url).includes('/webhooks/')),
    ).toEqual([]);
    expect(await interaction(data, 0, { message })).toEqual({ type: 6 });
    expect((await current()).state).toBe('check_in_failed');
    const callbacks = vi
      .mocked(fetch)
      .mock.calls.filter(([url]) => String(url).includes('/webhooks/'));
    expect(callbacks).toHaveLength(1);
    expect(callbacks[0][1]?.method).toBe('DELETE');
  });
  it('acknowledges signed additive stakes and replays a lost response even after close', async () => {
    const s = settings();
    s.queues[0].checkIn.enabled = false;
    s.queues[0].modules = { ...emptyModules, gold: true, predictions: true };
    await configure(s);
    for (let n = 0; n < 4; n++) await act('add', n);
    const m = await current();
    const state = await stub().prediction(guild, channel, person(8).id, m.id);
    if (!state.ok) throw Error(state.content);
    const custom_id = `bs:${state.book!.id}:a:${person(8).id}:50`,
      interactionId = '100000000000003333';
    const reply = await interaction({ custom_id }, 8, { id: interactionId });
    expect(reply.data.content).toBe(
      'Added 50 gold to Alpha. Your bet: 50 gold. You hold 450 gold.',
    );
    expect(reply).toMatchObject({ type: 4, data: { flags: 64 } });
    expect(reply.data.components?.flatMap((r) => r.components.map((b: any) => b.label))).toContain(
      '50 gold',
    );
    await act('bet_close', Number(BigInt(m.captains[0]) - 100000000000000100n), { matchId: m.id });
    const retry = await interaction({ custom_id }, 8, { id: interactionId });
    expect(retry.data.content).toBe(reply.data.content);
    expect(
      (await stub().records(guild)).economy.wallets.find((w) => w.user_id === person(8).id)
        ?.balance,
    ).toBe(500);
  });
  it('updates the owned private stake panel with cumulative stakes and cancellation', async () => {
    const s = settings();
    s.queues[0].checkIn.enabled = false;
    s.queues[0].modules = { ...emptyModules, gold: true, predictions: true };
    await configure(s);
    for (let n = 0; n < 4; n++) await act('add', n);
    const m = await current(),
      state = await stub().prediction(guild, channel, person(8).id, m.id);
    if (!state.ok) throw Error(state.content);
    const privateMessage = { message: { flags: 64, author: { id: app } } },
      stake = { custom_id: `bs:${state.book!.id}:a:${person(8).id}:50` };
    await interaction(stake, 8, privateMessage);
    const second = await interaction(stake, 8, privateMessage);
    expect(second.type).toBe(7);
    expect(second.data.flags).toBeUndefined();
    expect(second.data.content).toBe(
      'Added 50 gold to Alpha. Your bet: 100 gold. You hold 400 gold.',
    );
    const reopened = await interaction({ custom_id: `b:${m.id}:pick` }, 8);
    expect(reopened.type).toBe(4);
    expect(reopened.data.content).toContain('Back Alpha');
    expect(reopened.data.content).toContain('Your bet: 100 gold');
    const cancelled = await interaction(
      { custom_id: `bc:${state.book!.id}:${person(8).id}` },
      8,
      privateMessage,
    );
    expect(cancelled.type).toBe(7);
    expect(cancelled.data.content).toContain('Bet cancelled. Your gold was returned.');
    expect(cancelled.data.content).toContain('You hold 500 gold. Who will win?');
    const wallet = (await stub().records(guild)).economy.wallets.find(
      (w) => w.user_id === person(8).id,
    );
    expect(wallet?.balance).toBe(500);
  });
  it('never updates a copied or public stake panel and clears closed private controls', async () => {
    const s = settings();
    s.queues[0].checkIn.enabled = false;
    s.queues[0].modules = { ...emptyModules, gold: true, predictions: true };
    await configure(s);
    for (let n = 0; n < 4; n++) await act('add', n);
    const m = await current(),
      state = await stub().prediction(guild, channel, person(8).id, m.id);
    if (!state.ok) throw Error(state.content);
    const copied = await interaction({ custom_id: `bp:${state.book!.id}:a:${person(8).id}` }, 9, {
      message: { flags: 64, author: { id: app } },
    });
    expect(copied).toMatchObject({ type: 4, data: { flags: 64 } });
    const publicPanel = await interaction(
      { custom_id: `bp:${state.book!.id}:a:${person(8).id}` },
      8,
      { message: { flags: 0, author: { id: app } } },
    );
    expect(publicPanel).toMatchObject({ type: 4, data: { flags: 64 } });
    await act('bet_close', Number(BigInt(m.captains[0]) - 100000000000000100n), { matchId: m.id });
    const closed = await interaction(
      { custom_id: `bs:${state.book!.id}:a:${person(8).id}:50` },
      8,
      { message: { flags: 64, author: { id: app } } },
    );
    expect(closed.type).toBe(7);
    expect(closed.data.components).toEqual([]);
    expect(
      (await stub().records(guild)).economy.wallets.find((w) => w.user_id === person(8).id)
        ?.balance,
    ).toBe(500);
  });
  it('offers cancellation but no new stakes when predictions are disabled mid-match', async () => {
    const s = settings();
    s.queues[0].checkIn.enabled = false;
    s.queues[0].modules = { ...emptyModules, gold: true, predictions: true };
    await configure(s);
    for (let n = 0; n < 4; n++) await act('add', n);
    const m = await current(),
      state = await stub().prediction(guild, channel, person(0).id, m.id);
    if (!state.ok) throw Error(state.content);
    const saved = await stub().read(guild);
    saved.settings.queues[0].modules.predictions = false;
    await stub().save(guild, saved.revision, saved.settings, person(99).id, 'save');
    const panel = await interaction({ custom_id: `b:${m.id}:pick` });
    expect(panel.data.content).toContain('New bets are disabled');
    expect(panel.data.components?.flatMap((r) => r.components.map((b: any) => b.label))).toEqual([
      'Cancel bet',
    ]);
  });
  it('silently acknowledges Join and Leave for only the signed member, including duplicate clicks', async () => {
    await configure();
    const reply = await interaction({ custom_id: `q:${queue}:join:g14` });
    expect(reply).toEqual({ type: 6 });
    expect(await interaction({ custom_id: `q:${queue}:join` })).toEqual({ type: 6 });
    expect((await stub().records(guild)).waiting[0].players.map((p) => p.id)).toEqual([
      person(0).id,
    ]);
    expect(await interaction({ custom_id: `q:${queue}:leave:g14` })).toEqual({ type: 6 });
    expect(await interaction({ custom_id: `q:${queue}:leave` })).toEqual({ type: 6 });
    expect((await stub().records(guild)).waiting[0].players).toHaveLength(0);
  });
  it('still explains rejected Join clicks privately', async () => {
    const s = settings();
    s.queues[0].enabled = false;
    await configure(s);
    const reply = await interaction({ custom_id: `q:${queue}:join` });
    expect(reply).toMatchObject({ type: 4, data: { flags: 64 } });
    expect(reply.data.content).toContain('closed');
    expect((await stub().records(guild)).waiting[0].players).toHaveLength(0);
  });
  it('acknowledges Ping silently and publishes an invitation without a private success receipt', async () => {
    const s = settings();
    s.queues[0].voicePreview = true;
    const button = queueCard(s.queues[0], [])
      .components.flatMap((row) => row.components)
      .find((c) => c.type === 2 && c.label === 'Ping');
    if (!button?.custom_id) throw Error('The queue must offer a Ping button.');
    await configure(s);
    await runInDurableObject(env.VOICE_PRESENCE.getByName(env.DISCORD_APPLICATION_ID!), (i) => {
      const p = (i as any).snapshot;
      p.initialize(
        guild,
        [{ user_id: person(1).id, channel_id: channel, member: { user: { id: person(1).id } } }],
        {
          channels: [{ id: channel, type: 2 }],
          roles: [{ id: guild, permissions: '1024' }],
        },
      );
      p.healthy = true;
      (i as any).socket = {};
    });
    expect(await interaction({ custom_id: `${button.custom_id}:g14` })).toEqual({ type: 6 });
    const posts = vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === 'POST');
    expect(
      posts.some(
        ([url, init]) =>
          String(url).includes('/channels/') &&
          JSON.parse(init!.body as string).allowed_mentions.users?.includes(person(1).id),
      ),
    ).toBe(true);
    expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes('/webhooks/'))).toBe(
      false,
    );
    vi.mocked(fetch).mockClear();
    expect(await interaction({ custom_id: `q:${queue}:ping:g14` })).toEqual({ type: 6 });
    const errors = vi
      .mocked(fetch)
      .mock.calls.filter(([url]) => String(url).includes('/webhooks/'));
    expect(errors).toHaveLength(1);
    expect(errors[0][1]?.method).toBe('POST');
    expect(String(errors[0][0])).not.toContain('@original');
    expect(JSON.parse(errors[0][1]!.body as string)).toMatchObject({
      flags: 64,
      content: expect.stringContaining('recently'),
      allowed_mentions: { parse: [] },
    });
  });
  it('rejects Ping buttons copied into another channel with a private followup and no invitation', async () => {
    const s = settings();
    s.queues[0].voicePreview = true;
    await configure(s);
    expect(
      await interaction({ custom_id: `q:${queue}:ping` }, 0, { channel_id: '706320010368516299' }),
    ).toEqual({ type: 6 });
    const writes = vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === 'POST');
    expect(writes).toHaveLength(1);
    expect(String(writes[0][0])).toContain('/webhooks/');
    expect(JSON.parse(writes[0][1]!.body as string).content).toContain('No matching queue');
  });
  it('routes unnamed joins to the channel default and keeps explicit buttons queue-specific', async () => {
    const s = settings();
    s.queues.push({ ...s.queues[0], id: otherQueue, name: 'Nomad' });
    await configure(s);
    expect(await interaction({ name: 'add' }, 0, { type: 2 })).toEqual({
      type: 5,
      data: { flags: 64 },
    });
    await interaction({ custom_id: `q:${otherQueue}:join` }, 1);
    expect(
      (await stub().records(guild)).waiting.find((q) => q.queueId === otherQueue)!.players,
    ).toHaveLength(1);
  });

  it('bounds queue picker pages at Discord’s 25-option limit', () => {
    const qs = Array.from({ length: 200 }, (_, i) => ({
      ...settings().queues[0],
      name: `Queue ${i}`,
    }));
    for (let page = 0; page < 8; page++) {
      const panel = queuePicker(qs, 'add', page);
      expect((panel.components[0].components[0] as any).options).toHaveLength(25);
      expect(panel.components[1].components).toHaveLength(2);
    }
    expect(queuePicker([], 'add').components).toHaveLength(0);
  });
  it('rejects copied buttons in another guild or channel, and paused queues remain leaveable', async () => {
    await configure();
    await interaction({ custom_id: `q:${queue}:join` });
    const foreign = await interaction({ custom_id: `q:${queue}:join` }, 1, {
      channel_id: '706320010368516201',
    });
    expect(foreign.data.content).toContain('No matching queue');
    await interaction({ custom_id: `q:${queue}:join` }, 1, { guild_id: '706320010368516199' });
    const snapshot = await stub().read(guild);
    snapshot.settings.queues[0].enabled = false;
    await stub().save(guild, snapshot.revision, snapshot.settings, person(99).id, 'save');
    expect((await interaction({ custom_id: `q:${queue}:join` }, 1)).data.content).toContain(
      'closed',
    );
    expect(await interaction({ custom_id: `q:${queue}:leave` })).toEqual({ type: 6 });
    expect((await stub().records(guild)).waiting[0].players).toHaveLength(0);
  });
  it('retires old substitution panels while preserving manual request intent', async () => {
    const m = await fill(true);
    expect((await click(m, 'subs')).data.content).toContain('current match');
    await click(m, 'request');
    await click(m, 'request');
    expect((await current()).subRequests).toEqual([person(0).id]);
    expect(matchCard(await current()).embeds[0].description).toContain(
      `**Looking for a substitute:** <@${person(0).id}>`,
    );
    await click(m, 'cancel');
    await click(m, 'cancel');
    expect((await current()).subRequests).toEqual([]);
    expect(matchCard(await current()).embeds[0].description).not.toContain(
      'Looking for a substitute',
    );
  });

  it('rejects retired volunteer selectors without changing a roster', async () => {
    const m = await fill(true);
    await click(m, 'take', 8, [person(0).id]);
    expect((await current()).roster.map((p) => p.id)).toEqual(m.roster.map((p) => p.id));
  });

  it('makes Sub me direct and denies spectators', async () => {
    const m = await fill(true);
    await act('add', 8);
    expect((await click(m, 'subauto', 9)).data.content).toContain('outgoing player');
    expect(await click(m, 'subauto', 0)).toEqual({ type: 6 });
    expect((await current()).roster.some((p) => p.id === person(8).id)).toBe(true);
  });

  it.each([false, true])('retires the old Cancel match button even for admin=%s', async (admin) => {
    const m = await fill(true);
    const n = Number(BigInt(m.captains[0]) - 100000000000000100n);
    const reply = await interaction({ custom_id: `m:${m.id}:${m.version}:abort` }, n, {
      member: {
        permissions: admin ? '32' : '0',
        user: { id: person(n).id, username: person(n).name },
      },
    });
    expect(reply.data.flags).toBe(64);
    expect(reply.data.content).toContain('`/report abort`');
    expect(await current()).toEqual(m);
  });

  it('starts cancellation through the command and keeps opposing-captain agreement', async () => {
    const m = await fill(true);
    const command = { name: 'report', options: [{ type: 1, name: 'abort' }] };
    await interaction(command, 9, { type: 2 });
    expect(await current()).toEqual(m);
    const captain = Number(BigInt(m.captains[0]) - 100000000000000100n);
    const opposing = Number(BigInt(m.captains[1]) - 100000000000000100n);
    await interaction(command, captain, { type: 2 });
    const proposed = await current();
    expect(proposed.state).toBe('playing');
    expect(proposed.proposal).toMatchObject({
      kind: 'abort',
      requester: m.captains[0],
      opposing: m.captains[1],
    });
    const agreement = {
      custom_id: `a:${m.id}:${proposed.version}:${proposed.proposal!.id}:abort`,
    };
    await interaction(agreement, captain);
    expect((await current()).state).toBe('playing');
    await interaction(agreement, opposing);
    expect((await current()).state).toBe('aborted');
  });

  it('lets an administrator cancel immediately through the command', async () => {
    await fill(true);
    await interaction({ name: 'report', options: [{ type: 1, name: 'abort' }] }, 99, {
      type: 2,
      member: {
        permissions: '32',
        user: { id: person(99).id, username: person(99).name },
      },
    });
    expect((await current()).state).toBe('aborted');
  });

  it('returns the original direct substitution receipt after a lost response', async () => {
    const m = await fill(true);
    await act('add', 8);
    const id = String(seq++),
      data = { custom_id: `m:${m.id}:${m.version}:subauto` };
    const saved = await interaction(data, 0, { id });
    expect(saved).toEqual({ type: 6 });
    expect(await interaction(data, 0, { id })).toEqual(saved);
    expect((await interaction(data, 9, { id })).data.content).toContain('different input');
    expect((await current()).roster.filter((p) => p.id === person(8).id)).toHaveLength(1);
  });

  it('shows waiting expiry on the queue card and silently refreshes slash membership', async () => {
    const s = settings();
    s.queues[0].autoStart = false;
    s.queues[0].controls = {
      waitingSeconds: 60,
      matchSeconds: 0,
      requiredRole: null,
      blockedRole: null,
    };
    await configure(s);
    const joined = await interaction({ name: 'add' }, 0, { type: 2 });
    expect(joined).toEqual({ type: 5, data: { flags: 64 } });
    expect(queueCard(s.queues[0], [person(0)]).embeds[0].description).toContain(
      'expire after 60 seconds',
    );
    await runInDurableObject(stub(), (_i, state) => {
      for (let n = 1; n < 4; n++)
        state.storage.sql.exec(
          'INSERT INTO queue_entries(queue_id,user_id,display_name) VALUES(?,?,?)',
          queue,
          person(n).id,
          person(n).name,
        );
    });
    const duplicate = await interaction({ name: 'add' }, 0, { type: 2 });
    expect(duplicate).toEqual({ type: 5, data: { flags: 64 } });
    expect((await stub().records(guild)).waiting[0].players).toHaveLength(4);
  });
  it('records loss immediately and rechecks captain authority', async () => {
    const m = await fill(true),
      n = Number(BigInt(m.captains[0]) - 100000000000000100n);
    await click(m, 'loss', 9);
    expect((await current()).state).toBe('playing');
    const reply = await click(m, 'loss', n);
    expect(reply).toEqual({ type: 6 });
    expect(await current()).toMatchObject({ state: 'finished', result: 'beta' });
    await click(m, 'loss', n);
    expect((await stub().records(guild)).changes).toHaveLength(4);
  });

  it('confirms backing out, forbids spectator back-outs, and routes rejoin to the saved queue', async () => {
    const m = await fill();
    await click(m, 'ready', 1);
    expect((await click(m, 'back')).data.content).toContain('Leave this match?');
    expect((await current()).state).toBe('check_in');
    await click(m, 'confirm_back', 9);
    expect((await current()).state).toBe('check_in');
    await click(m, 'confirm_back');
    const closed = await current();
    expect(closed.state).toBe('check_in_failed');
    expect((await click(closed, 'rejoin')).data.content).toContain('current match');
    await interaction({ custom_id: `q:${queue}:join` });
    expect((await stub().records(guild)).waiting[0].players.map((p) => p.id)).toContain(
      person(0).id,
    );
  });
  it('shows the real saved match privately and rejects stale panel versions and foreign channels', async () => {
    const m = await fill(true);
    const teams = await interaction({ name: 'teams' }, 0, { type: 2 });
    expect(teams.data).toMatchObject({
      flags: 64,
      embeds: [{ title: 'Pickup · RTS · awaiting result' }],
    });
    expect((await interaction({ custom_id: `m:${m.id}:999:subs` })).data.content).toContain(
      'match changed',
    );
    expect(
      (
        await interaction({ custom_id: `m:${m.id}:${m.version}:subs` }, 0, {
          channel_id: '706320010368516201',
        })
      ).data.content,
    ).toContain('match’s channel');
  });
});

describe('quiet durable message delivery', () => {
  it('notifies the selected roster on the first match delivery, then edits without pings', async () => {
    const m = await fill();
    await runInDurableObject(stub(), (_i, s) => {
      s.storage.sql.exec("UPDATE outbox SET due=0 WHERE status='pending'");
    });
    await stub().flush();
    const delivered = vi
      .mocked(fetch)
      .mock.calls.filter(([, init]) => init?.method === 'POST')
      .map(([, init]) => JSON.parse(init!.body as string));
    const card = delivered.find((p) => p.embeds?.[0]?.title.includes('check in'));
    expect(card.allowed_mentions.users).toEqual(m.roster.map((p) => p.id));
    await click(m, 'ready');
    await runInDurableObject(stub(), (_i, s) => {
      s.storage.sql.exec("UPDATE outbox SET due=0 WHERE status='pending'");
    });
    await stub().flush();
    const edit = vi
      .mocked(fetch)
      .mock.calls.filter(
        ([url, init]) => init?.method === 'PATCH' && String(url).includes('/messages/'),
      )
      .map(([, init]) => JSON.parse(init!.body as string))
      .at(-1);
    expect(edit.allowed_mentions).toEqual({ parse: [] });
    expect(edit.content).toBe('');
  });
  it('reconciles a card by hidden component identity without visible UUIDs or duplicate posts', async () => {
    await configure();
    await act('add');
    vi.mocked(fetch).mockImplementationOnce(async () => {
      throw new Error('lost receipt');
    });
    await runInDurableObject(stub(), (_i, s) => {
      s.storage.sql.exec("UPDATE outbox SET due=0 WHERE status='pending'");
    });
    await stub().flush();
    await runInDurableObject(stub(), (_i, state) =>
      state.storage.sql.exec('UPDATE outbox SET due=0'),
    );
    vi.mocked(fetch).mockImplementationOnce(async () =>
      Response.json([
        {
          id: '100000000000008887',
          author: { id: '100000000000009999' },
          embeds: [{ footer: { text: 'Community Games' } }],
          components: [{ components: [{ custom_id: `q:${queue}:join:g1` }] }],
        },
        {
          id: '100000000000008888',
          author: { id: app },
          embeds: [{ footer: { text: 'Community Games' } }],
          components: [{ components: [{ custom_id: `q:${queue}:join:g1` }] }],
        },
      ]),
    );
    await runInDurableObject(stub(), (_i, s) => {
      s.storage.sql.exec("UPDATE outbox SET due=0 WHERE status='pending'");
    });
    await stub().flush();
    expect(vi.mocked(fetch).mock.calls.filter(([, i]) => i?.method === 'POST')).toHaveLength(1);
    expect(vi.mocked(fetch).mock.calls.filter(([, i]) => i?.method === 'PATCH')).toHaveLength(1);
  });
});

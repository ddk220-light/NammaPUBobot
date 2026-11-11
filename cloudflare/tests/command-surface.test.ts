/// <reference types="vite/client" />
import { describe, expect, it, vi } from 'vitest';
import { interactions } from '../src/discord/interactions';
import { adminCommand, adminCommandRoute, commandsFor } from '../src/discord/commands.mjs';
import { helpText } from '../src/discord/help';
import { identity } from '../src/identity';
import type { Env } from '../src/env';
import type { Settings } from '../src/domain/config';
import { channel, guild, person, settings } from './harness';

const application = '100000000000000001';
const matchId = '7f1c2a8e-2d1b-4a7e-9c3f-0a1b2c3d4e5f';
const role = '100000000000000777';
const organizer = { permissions: '32' };
const everyone = [
  'help',
  'add',
  'remove',
  'queue',
  'teams',
  'report',
  'subme',
  'subfor',
  'rank',
  'leaderboard',
  'gold',
  'profile',
  'lobby',
  'civpick',
  'admin',
];
type Node = { type?: number; name: string; options?: Node[] };
const subcommands = (c: object) =>
  ((c as Node).options ?? []).filter((o) => o.type === 1 || o.type === 2).map((o) => o.name);

async function signed(data: object, member: object = {}) {
  const raw = JSON.stringify({
    id: '100000000001234567',
    type: 2,
    application_id: application,
    guild_id: guild,
    channel_id: channel,
    token: 'synthetic-interaction',
    member: { permissions: '0', user: { id: person(0).id, username: 'Player' }, ...member },
    data,
  });
  const timestamp = String(Math.floor(Date.now() / 1000));
  // Public RFC 8032 fixture, also used by the repository's existing signature tests.
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
  const signature = new Uint8Array(
    await crypto.subtle.sign('Ed25519', key, new TextEncoder().encode(timestamp + raw)),
  );
  return new Request('https://setup.example/interactions', {
    method: 'POST',
    body: raw,
    headers: {
      'X-Signature-Timestamp': timestamp,
      'X-Signature-Ed25519': Array.from(signature, (b) => b.toString(16).padStart(2, '0')).join(''),
    },
  });
}
function context(config: Settings = settings()) {
  const tasks: Promise<unknown>[] = [];
  const rpc = {
    read: vi.fn(async () => ({ settings: config })),
    play: vi.fn(async () => ({ ok: true, content: 'Done' })),
    rally: vi.fn(async () => ({ ok: true, content: 'Rallied' })),
    goldView: vi.fn(async () => ({ ok: true, content: 'Gold board' })),
    flush: vi.fn(async () => {}),
  };
  return {
    rpc,
    tasks,
    ctx: { waitUntil: (p: Promise<unknown>) => tasks.push(p) },
    env: {
      APP_ORIGIN: 'https://setup.example',
      DISCORD_APPLICATION_ID: application,
      DISCORD_PUBLIC_KEY: 'd75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a',
      COMMUNITIES: { getByName: vi.fn(() => rpc) },
    } as unknown as Env,
  };
}
type Reply = { type: number; data: { content: string; flags: number; components?: any[] } };
async function run(c: ReturnType<typeof context>, data: object, member: object = {}) {
  const reply = (await (
    await interactions(await signed(data, member), c.env, c.ctx)
  ).json()) as Reply;
  await Promise.all(c.tasks);
  return reply;
}
const manage = (group: string, action: string, args: Record<string, unknown>) => ({
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
    users: { [person(1).id]: { id: person(1).id, username: 'Player 1' } },
    members: { [person(1).id]: { roles: [] } },
  },
});

describe('registered command surface', () => {
  it('registers the same small list for every environment, plus /test outside production', () => {
    expect(commandsFor('production').map((c) => c.name)).toEqual([...everyone, 'manage']);
    expect(commandsFor('staging').map((c) => c.name)).toEqual(['test', ...everyone, 'manage']);
    expect(commandsFor('local')).toEqual(commandsFor('staging'));
    const visible = commandsFor('production').filter((c) => c.default_member_permissions !== '32');
    expect(visible.map((c) => c.name)).toEqual(everyone);
  });

  it('lists the public subcommands and choices', () => {
    const find = (name: string) => commandsFor('production').find((c) => c.name === name)!;
    expect(subcommands(find('queue'))).toEqual(['show', 'start']);
    expect(subcommands(find('report'))).toEqual(['loss', 'draw', 'abort']);
    expect(subcommands(find('profile'))).toEqual([
      'link',
      'unlink',
      'show',
      'verify',
      'hide',
      'unhide',
    ]);
    expect(subcommands(find('lobby'))).toEqual([
      'list',
      'attach',
      'unlink',
      'status',
      'confirm',
      'facts',
      'replays',
    ]);
    const view = find('gold').options!.find((o) => o.name === 'view') as unknown as {
      choices: { value: string }[];
    };
    expect(view.choices.map((c) => c.value)).toEqual([
      'balance',
      'history',
      'richest',
      'predictors',
    ]);
    // Organizers add other players through /manage queue add.
    expect(find('add').options!.map((o) => o.name)).toEqual(['queue']);
  });

  it('uses plain public descriptions within Discord limits', () => {
    const walk = (o: any, path: string): void => {
      expect(o.description.length, path).toBeGreaterThan(0);
      expect(o.description.length, path).toBeLessThanOrEqual(100);
      expect(o.name, path).toMatch(/^[a-z0-9_-]{1,32}$/);
      if (!['test', 'manage'].includes(path.split(' ')[0]))
        expect(o.description, path).not.toMatch(/^(Administrator|Captain\/admin|Organizer):/);
      for (const x of o.options ?? []) walk(x, `${path} ${x.name}`);
      expect(o.options?.length ?? 0).toBeLessThanOrEqual(25);
      expect(o.choices?.length ?? 0).toBeLessThanOrEqual(25);
    };
    for (const c of commandsFor('staging')) walk(c, c.name);
  });

  it('keeps every removed command reachable through /manage or a public replacement', () => {
    expect(adminCommandRoute('queue', 'rally')).toMatchObject({ command: 'rally' });
    expect(adminCommandRoute('match', 'sub-from-queue')).toMatchObject({
      command: 'subauto',
      required: ['user', 'match'],
    });
    expect(adminCommandRoute('match', 'maps-redo')).toMatchObject({
      command: 'maps',
      subcommand: 'redo',
    });
    expect(adminCommandRoute('match', 'close-betting')).toMatchObject({
      command: 'predictions',
      subcommand: 'close',
    });
    for (const group of adminCommand.options)
      for (const action of group.options)
        expect(adminCommandRoute(group.name, action.name)).not.toBeNull();
  });
});

describe('routes for removed commands', () => {
  it.each([
    ['match', 'sub-from-queue', { match: matchId, user: person(1).id }, 'subauto'],
    ['match', 'maps-redo', { match: matchId }, 'map_redo'],
    ['match', 'close-betting', { match: matchId }, 'bet_close'],
  ])('/manage %s %s dispatches %s', async (group, action, args, kind) => {
    const c = context();
    await run(c, manage(group, action, args), organizer);
    expect(c.rpc.play).toHaveBeenCalledWith(
      guild,
      expect.objectContaining({
        kind,
        matchId,
        ...(kind === 'subauto' ? { target: expect.objectContaining({ id: person(1).id }) } : {}),
      }),
    );
  });

  it('/manage queue rally invites voice participants', async () => {
    const c = context();
    await run(c, manage('queue', 'rally', {}), organizer);
    expect(c.rpc.rally).toHaveBeenCalled();
  });

  it('/manage match sub-from-queue requires the player and match', async () => {
    const c = context();
    const reply = await run(c, manage('match', 'sub-from-queue', { match: matchId }), organizer);
    expect(reply.data.content).toContain('required options');
    expect(c.rpc.play).not.toHaveBeenCalled();
  });

  it.each([
    ['richest', 'leaderboard'],
    ['predictors', 'predictions'],
    ['leaderboard', 'leaderboard'],
    [undefined, 'balance'],
  ])('/gold view:%s reads the %s board', async (choice, view) => {
    const c = context();
    const reply = await run(c, {
      name: 'gold',
      options: choice ? [{ name: 'view', value: choice }] : [],
    });
    expect(reply.data.content).toBe('Gold board');
    expect(c.rpc.goldView).toHaveBeenCalledWith(
      guild,
      channel,
      person(0).id,
      view,
      undefined,
      false,
    );
  });
});

describe('/help', () => {
  const button = (reply: Reply) => reply.data.components?.[0]?.components?.[0];

  it('explains playing to a member, privately and without organizer tools', async () => {
    const c = context();
    const reply = await run(c, { name: 'help' });
    expect(reply).toMatchObject({ type: 4, data: { flags: 64, allowed_mentions: { parse: [] } } });
    const text = reply.data.content;
    expect(text).toContain(identity.publicName);
    for (const part of ['/add', '/remove', 'Check in', 'Report result', '/report', '/rank'])
      expect(text).toContain(part);
    for (const part of ['/leaderboard', '/profile link', '/profile hide'])
      expect(text).toContain(part);
    expect(text).not.toContain('/manage');
    expect(button(reply)).toBeUndefined();
    expect(text.length).toBeLessThan(900);
  });

  it('adds organizer pointers and the web console for Manage Server', async () => {
    const reply = await run(context(), { name: 'help' }, { permissions: '32' });
    expect(reply.data.content).toContain('/admin');
    expect(reply.data.content).toContain('/manage');
    expect(button(reply)).toEqual({
      type: 2,
      style: 5,
      label: 'Open web console',
      url: `https://setup.example/app/?community=${guild}`,
    });
  });

  it('points a bot moderator role at /admin only, which is all it can use', async () => {
    const config = settings();
    config.queues[0].adminRoles = [role];
    const reply = await run(context(config), { name: 'help' }, { roles: [role] });
    expect(reply.data.content).toContain('/admin');
    expect(reply.data.content).not.toContain('/manage');
    expect(button(reply)).toBeUndefined();
  });

  it('says when the channel has no queue and points organizers to setup', async () => {
    const config = settings();
    config.queues[0].channelId = '706320010368516299';
    const member = await run(context(config), { name: 'help' });
    expect(member.data.content).toContain('no queue yet');
    expect(member.data.content).toContain('organizer');
    expect(member.data.content).not.toContain('/add');
    expect(button(member)).toBeUndefined();
    const organizer = await run(context(config), { name: 'help' }, { permissions: '8' });
    expect(organizer.data.content).toContain('no queue yet');
    expect(button(organizer)?.url).toContain(`community=${guild}`);
  });

  it('describes channels without check-in and with several queues', () => {
    const q = settings().queues[0];
    const text = helpText(
      [
        { ...q, checkIn: { enabled: false, seconds: 60 } },
        { ...q, name: 'Ranked', checkIn: { enabled: false, seconds: 60 } },
      ],
      false,
    );
    expect(text).toContain('start as soon as the queue fills');
    expect(text).toContain('2 queues (Pickup, Ranked)');
  });
});

describe('reply text names only registered commands', () => {
  const sources = import.meta.glob(['../src/**/*.ts', '../src/**/*.mjs'], {
    query: '?raw',
    import: 'default',
    eager: true,
  }) as Record<string, string>;
  const registered = new Map(commandsFor('staging').map((c) => [c.name, c]));
  // Names a reply could still mention: every registered command plus the retired ones.
  const known = new Set([
    ...registered.keys(),
    ...['setup', 'rating', 'match', 'moderation', 'rally', 'subauto', 'maps', 'predictions'],
  ]);
  // Words that name a subcommand somewhere, so "/test is not" reads as prose, not a path.
  const tree = (o: any): string[] => [
    ...subcommands(o),
    ...(o.options ?? []).flatMap((x: any) => (x.type === 2 ? tree(x) : [])),
  ];
  const actions = new Set([
    ...[...registered.values()].flatMap(tree),
    ...['link', 'redo', 'close', 'leaderboard', 'reset', 'result', 'maps', 'status'],
  ]);
  const knownStale = new Set<string>();

  it('finds the sources', () => {
    expect(Object.keys(sources).length).toBeGreaterThan(20);
  });

  it('has no reference to a removed command or subcommand', () => {
    const problems: string[] = [];
    for (const [file, raw] of Object.entries(sources)) {
      const code = raw.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|\s)\/\/.*$/gm, '$1');
      for (const m of code.matchAll(
        /(?<=^|[\s`'"(])\/([a-z]+)(?![\w/-])(?: ([a-z][a-z-]*))?(?: ([a-z][a-z-]*))?/gm,
      )) {
        const [text, name, first, second] = m;
        if (!known.has(name)) continue;
        const command = registered.get(name);
        const reference = `/${[name, first].filter(Boolean).join(' ')}`;
        if (!command) problems.push(`${file}: ${text}`);
        else if (
          subcommands(command).length &&
          first &&
          actions.has(first) &&
          !knownStale.has(reference)
        ) {
          const group = command.options!.find((o) => o.name === first);
          if (!group) problems.push(`${file}: ${text}`);
          else if (group.type === 2 && second && !subcommands(group).includes(second))
            problems.push(`${file}: ${text}`);
        }
      }
    }
    expect(problems).toEqual([]);
  });
});

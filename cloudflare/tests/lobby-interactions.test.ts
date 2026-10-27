import { describe, expect, it, vi } from 'vitest';
import { interactions } from '../src/discord/interactions';
import { commands } from '../src/discord/commands.mjs';
import type { Env } from '../src/env';
import { harness, settings, guild, channel, game, person } from './harness';

harness();
const application = '100000000000000001';
async function request(name: string, options: object[]) {
  const raw = JSON.stringify({
    id: '100000000001234567',
    type: 2,
    application_id: application,
    guild_id: guild,
    channel_id: channel,
    token: 'synthetic-interaction',
    member: { permissions: '0', user: { id: person(0).id, username: 'Player' } },
    data: { name: 'lobby', options: [{ name, type: 1, options }] },
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
function context() {
  const tasks: Promise<unknown>[] = [];
  const rpc = {
    read: vi.fn(async () => ({ settings: settings() })),
    play: vi.fn(async () => ({ ok: true, content: 'Saved lobby' })),
    linkGameProfile: vi.fn(async () => ({ ok: true, content: 'Self-declared profile saved' })),
    flush: vi.fn(async () => {}),
  };
  const getByName = vi.fn(() => rpc);
  return {
    rpc,
    tasks,
    getByName,
    ctx: { waitUntil: (p: Promise<unknown>) => tasks.push(p) },
    env: {
      DISCORD_APPLICATION_ID: application,
      DISCORD_PUBLIC_KEY: 'd75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a',
      COMMUNITIES: { getByName },
    } as unknown as Env,
  };
}
describe('lobby command transport', () => {
  it('registers one game-neutral command with attach/unlink/status/profile linking', () => {
    expect(commands.find((c) => c.name === 'lobby')!.options!.map((o) => o.name)).toEqual([
      'attach',
      'unlink',
      'status',
      'link',
    ]);
  });
  it.each(['attach', 'unlink', 'status'])(
    'defers %s and forwards the explicit match and signed caller',
    async (name) => {
      const c = context(),
        matchId = crypto.randomUUID();
      const options = [
        { name: 'match', value: matchId },
        ...(name === 'attach' ? [{ name: 'reference', value: '9001' }] : []),
      ];
      const response = await interactions(await request(name, options), c.env, c.ctx);
      expect(await response.json()).toMatchObject({ type: 5, data: { flags: 64 } });
      await Promise.all(c.tasks);
      expect(c.getByName).toHaveBeenCalledWith(guild);
      expect(c.rpc.play).toHaveBeenCalledWith(
        guild,
        expect.objectContaining({
          kind: `lobby_${name}`,
          matchId,
          actor: expect.objectContaining({ id: person(0).id }),
          admin: false,
        }),
      );
      const replies = vi
        .mocked(fetch)
        .mock.calls.filter(([input]) => String(input).includes('/webhooks/'));
      expect(replies).toHaveLength(1);
      expect(JSON.parse(replies[0][1]!.body as string)).toMatchObject({
        content: 'Saved lobby',
        allowed_mentions: { parse: [] },
      });
    },
  );
  it('acknowledges profile linking while provider resolution is still pending', async () => {
    const c = context();
    let finish!: (value: { ok: boolean; content: string }) => void;
    c.rpc.linkGameProfile.mockImplementation(() => new Promise((resolve) => (finish = resolve)));
    const response = await interactions(
      await request('link', [{ name: 'profile_id', value: '123' }]),
      c.env,
      c.ctx,
    );
    expect(await response.json()).toMatchObject({ type: 5 });
    await vi.waitFor(() => expect(finish).toBeDefined());
    expect(c.rpc.linkGameProfile).toHaveBeenCalledWith(
      guild,
      expect.objectContaining({
        kind: 'link',
        gameId: game,
        externalId: '123',
        userId: person(0).id,
      }),
      person(0).id,
      false,
    );
    finish({ ok: true, content: 'Self-declared mapping saved' });
    await Promise.all(c.tasks);
  });
});

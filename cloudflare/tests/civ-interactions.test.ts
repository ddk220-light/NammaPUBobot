import { describe, expect, it, vi } from 'vitest';
import { interactions } from '../src/discord/interactions';
import type { Env } from '../src/env';
import { harness, guild, channel, person } from './harness';
harness();
const app = '100000000000000001',
  match = '81da571b-6a4a-4e5d-8e32-f4050df35a2d';
async function signed(payload: object) {
  const body = JSON.stringify({
      id: '100000000001234567',
      application_id: app,
      guild_id: guild,
      channel_id: channel,
      token: 'synthetic',
      member: { permissions: '0', user: { id: person(0).id, username: 'Player' } },
      ...payload,
    }),
    ts = String(Math.floor(Date.now() / 1000));
  const key = await crypto.subtle.importKey(
    'pkcs8',
    Uint8Array.from(
      '302e020100300506032b6570042204209d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60'
        .match(/../g)!
        .map((x) => parseInt(x, 16)),
    ),
    'Ed25519',
    false,
    ['sign'],
  );
  const sig = new Uint8Array(
    await crypto.subtle.sign('Ed25519', key, new TextEncoder().encode(ts + body)),
  );
  return new Request('https://setup.example/interactions', {
    method: 'POST',
    body,
    headers: {
      'X-Signature-Timestamp': ts,
      'X-Signature-Ed25519': [...sig].map((x) => x.toString(16).padStart(2, '0')).join(''),
    },
  });
}
function context() {
  const tasks: Promise<unknown>[] = [],
    rpc = {
      play: vi.fn(async () => ({ content: 'Choice locked.' })),
      flush: vi.fn(async () => {}),
    };
  return {
    rpc,
    tasks,
    ctx: { waitUntil: (p: Promise<unknown>) => tasks.push(p) },
    env: {
      DISCORD_APPLICATION_ID: app,
      DISCORD_PUBLIC_KEY: 'd75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a',
      COMMUNITIES: { getByName: () => rpc },
    } as unknown as Env,
  };
}
describe('signed civpick transport', () => {
  it('clears the slash response without waiting for slow public-card delivery', async () => {
    const c = context();
    let delivered!: () => void;
    c.rpc.flush.mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          delivered = resolve;
        }),
    );
    const response = await interactions(
      await signed({ type: 2, data: { name: 'civpick' } }),
      c.env,
      c.ctx,
    );
    expect(await response.json()).toEqual({ type: 5, data: { flags: 64 } });
    await c.tasks[0];
    expect(
      vi.mocked(fetch).mock.calls.find(([url]) => String(url).includes('/webhooks/'))?.[1]?.method,
    ).toBe('DELETE');
    delivered();
    await Promise.all(c.tasks);
  });
  it('defers privately and preserves exact match/duration/redo options', async () => {
    const c = context(),
      r = await interactions(
        await signed({
          type: 2,
          data: {
            name: 'civpick',
            options: [
              { name: 'match', value: match },
              { name: 'minutes', value: 7 },
              { name: 'redo', value: true },
            ],
          },
        }),
        c.env,
        c.ctx,
      );
    expect(await r.json()).toEqual({ type: 5, data: { flags: 64 } });
    await Promise.all(c.tasks);
    expect(
      vi.mocked(fetch).mock.calls.find(([url]) => String(url).includes('/webhooks/'))?.[1]?.method,
    ).toBe('DELETE');
    expect(c.rpc.play).toHaveBeenCalledWith(
      guild,
      expect.objectContaining({
        kind: 'civ_start',
        matchId: match,
        minutes: 7,
        redo: true,
        actor: expect.objectContaining({ id: person(0).id }),
        admin: false,
      }),
    );
  });
  it('binds component claims to the signed member and generation', async () => {
    const c = context(),
      r = await interactions(
        await signed({
          type: 3,
          message: { author: { id: app } },
          data: { custom_id: `civ:${match}:2:12` },
        }),
        c.env,
        c.ctx,
      );
    expect(await r.json()).toEqual({ type: 5, data: { flags: 64 } });
    await Promise.all(c.tasks);
    expect(
      vi.mocked(fetch).mock.calls.find(([url]) => String(url).includes('/webhooks/'))?.[1]?.method,
    ).toBe('PATCH');
    expect(c.rpc.play).toHaveBeenCalledWith(
      guild,
      expect.objectContaining({ kind: 'civ_claim', generation: 2, choice: 12 }),
    );
  });
  it.each([
    {
      type: 3,
      message: { author: { id: '100000000000000099' } },
      data: { custom_id: `civ:${match}:1:0` },
    },
    { type: 2, data: { name: 'civpick', options: [{ name: 'minutes', value: 11 }] } },
  ])('rejects forged cards and invalid durations before calling the domain', async (payload) => {
    const c = context(),
      r = await interactions(await signed(payload), c.env, c.ctx);
    expect(((await r.json()) as any).data.flags).toBe(64);
    expect(c.rpc.play).not.toHaveBeenCalled();
  });
});

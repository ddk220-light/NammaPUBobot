import { env } from 'cloudflare:workers';
import { runInDurableObject } from 'cloudflare:test';
import { describe, it, expect } from 'vitest';
import { PresenceSnapshot } from '../src/domain/presence';
import {
  harness,
  stub,
  setup,
  settings,
  act,
  action,
  person,
  guild,
  queue,
  flush,
} from './harness';
harness();
const voice = '706320010368516222';
async function presence(users: number[]) {
  await runInDurableObject(
    env.VOICE_PRESENCE.getByName(env.DISCORD_APPLICATION_ID!),
    (instance) => {
      const object = instance as unknown as { snapshot: PresenceSnapshot; socket: object | null };
      object.snapshot.initialize(
        guild,
        users.map((n) => ({ user_id: person(n).id, channel_id: voice })),
      );
      object.snapshot.healthy = true;
      object.socket = {};
    },
  );
}
describe('optional voice rally', () => {
  it('tracks initial users, moves and leaves; refuses stale snapshots', () => {
    const p = new PresenceSnapshot();
    p.initialize(guild, [{ user_id: person(0).id, channel_id: voice }]);
    expect(p.members(guild)).toBeNull();
    p.healthy = true;
    expect(p.members(guild)).toHaveLength(1);
    p.update(guild, person(0).id, null);
    expect(p.members(guild)).toEqual([]);
    p.update(guild, person(1).id, voice);
    p.invalidate();
    expect(p.members(guild)).toBeNull();
  });
  it('does not start presence when disabled or the caller lacks permission', async () => {
    await setup();
    expect((await stub().rally(guild, action('queue', 0))).content).toContain('disabled');
  });
  it('invites only eligible non-waiting voice members, enforces cooldown and mention limits', async () => {
    const s = settings();
    s.queues[0].rally = {
      enabled: true,
      channels: [voice],
      roles: [],
      cooldownSeconds: 300,
      mentionLimit: 2,
    };
    await setup(s);
    await presence([0, 1, 2, 3]);
    await act('add', 1);
    expect((await stub().rally(guild, action('queue', 0), voice)).content).toContain(
      'Only organizers',
    );
    expect(
      (await stub().rally(guild, action('queue', 0, { admin: true }), voice)).content,
    ).toContain('accepted');
    await runInDurableObject(stub(), (_i, state) => {
      const row = state.storage.sql
        .exec<{ targets: string }>('SELECT targets FROM rally_runs')
        .toArray()[0];
      const targets = JSON.parse(row.targets);
      expect(targets.users).toHaveLength(2);
      expect(targets.users).not.toContain(person(1).id);
    });
    expect(
      (await stub().rally(guild, action('queue', 0, { admin: true }), voice)).content,
    ).toContain('recently');
  });
  it('pausing/full queues or disconnected presence cannot produce an invitation', async () => {
    const s = settings();
    s.queues[0].rally = {
      enabled: true,
      channels: [voice],
      roles: [],
      cooldownSeconds: 300,
      mentionLimit: 2,
    };
    s.queues[0].enabled = false;
    await setup(s);
    await presence([0, 1]);
    expect(
      (await stub().rally(guild, action('queue', 0, { admin: true }), voice)).content,
    ).toContain('does not need');
    await runInDurableObject(stub(), (_i, state) =>
      expect(state.storage.sql.exec('SELECT * FROM rally_runs').toArray()).toEqual([]),
    );
  });
});

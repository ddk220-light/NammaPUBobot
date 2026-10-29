import { env } from 'cloudflare:workers';
import { runInDurableObject } from 'cloudflare:test';
import { describe, expect, it } from 'vitest';
import { harness, setup, settings, stub, guild, game, person } from './harness';

harness();
describe('retired catalog compatibility', () => {
  it.each(['northgard', 'tempest_rising', 'sc2', 'generals'] as const)(
    'rejects new %s setup on the server',
    async (id) => {
      const c = settings();
      c.games[0].catalogId = id;
      expect(await setup(c)).toMatchObject({
        ok: false,
        reason: expect.stringContaining('retired'),
      });
      expect((await stub().read(guild)).settings.games).toEqual([]);
    },
  );
  it('preserves an existing retired game and declared account across settings edits and backup restore', async () => {
    await setup();
    const c = settings();
    c.games[0].catalogId = 'northgard';
    await runInDurableObject(stub(), (_instance, state) => {
      state.storage.sql.exec(
        'UPDATE games SET config=? WHERE id=?',
        JSON.stringify(c.games[0]),
        game,
      );
    });
    const user = person(0).id;
    expect(
      await stub().linkGameProfile(
        guild,
        {
          id: crypto.randomUUID(),
          kind: 'link',
          gameId: game,
          userId: user,
          externalId: '76561198000000001',
        },
        user,
        false,
      ),
    ).toMatchObject({ ok: true });
    c.games[0].name = 'Historical game';
    expect(await stub().save(guild, 1, c, user, 'save')).toMatchObject({ ok: true });
    const backup = await stub().operationalBackup(guild);
    const recovery = env.COMMUNITIES.getByName('recovery:scope:' + guild);
    expect(await recovery.operationalRestore(guild, backup as never)).toMatchObject({
      restored: true,
    });
    expect((await recovery.read(guild)).settings.games[0]).toMatchObject({
      catalogId: 'northgard',
      name: 'Historical game',
    });
    expect(
      (await recovery.statistics(guild, game, user, 0, true, user)).player?.profiles[0].external_id,
    ).toBe('76561198000000001');
  });
});

import { runInDurableObject } from 'cloudflare:test';
import type { Env } from '../src/env';
import { describe, it, expect } from 'vitest';
import { env } from 'cloudflare:workers';
import { harness, stub, settings, guild, game, person, act } from './harness';
import { defaultRecords } from '../src/domain/records';
import { settingsSchema } from '../src/domain/config';
import { publicPages } from '../src/routes/public-pages';
import { changesBetween } from '../web/change-review';
harness();
describe('search publication and saved metadata', () => {
  it('keeps existing settings unlisted and validates explicit publishing', async () => {
    const s = settings();
    s.games[0].records = { ...defaultRecords, visibility: 'public', searchPublishing: true };
    expect(settingsSchema.safeParse(s).success).toBe(false);
    s.publicPage = { displayName: 'Our community' };
    expect(settingsSchema.safeParse(s).success).toBe(true);
    s.games[0].records.visibility = 'members';
    expect(settingsSchema.safeParse(s).success).toBe(false);
  });
  it('publishes stable opaque URLs, rejects stale saves and immediately revokes visibility', async () => {
    const s = settings();
    s.publicPage = { displayName: 'Our community' };
    s.games[0].records = { ...defaultRecords, visibility: 'public', searchPublishing: true };
    expect((await stub().save(guild, 0, s, person(99).id, 'save')).ok).toBe(true);
    const meta = await stub().siteMetadata();
    expect(meta.publication.pending).toBe(false);
    const entry = await env
      .PUBLIC_DIRECTORY!.getByName('directory')
      .resolve(meta.publication.community);
    expect(entry?.guild).toBe(guild);
    expect(meta.publication.community).not.toContain(guild);
    const url = `https://setup.example/c/${meta.publication.community}/${meta.publication.games[0].slug}/`;
    const first = await publicPages(new Request(url), env);
    expect(first.status).toBe(200);
    expect(first.headers.get('Cache-Control')).toBe('no-store');
    const html = await first.text();
    expect(html).toContain('Our community');
    expect(html).not.toContain(guild);
    expect((await stub().save(guild, 0, s, person(99).id, 'save')).ok).toBe(false);
    s.games[0].records.searchPublishing = false;
    await stub().save(guild, 1, s, person(99).id, 'save');
    // Even a stale directory entry cannot authorize publication.
    await env.PUBLIC_DIRECTORY!.getByName('directory').register(entry!);
    expect(
      (await env.PUBLIC_DIRECTORY!.getByName('directory').resolve(meta.publication.community))
        ?.revision,
    ).toBe(2);
    expect((await publicPages(new Request(url), env)).status).toBe(404);
    expect((await stub().siteMetadata()).publication.community).toBe(meta.publication.community);
  });
  it('commits configuration during directory failure and durably retries publication', async () => {
    const s = settings();
    s.publicPage = { displayName: 'A community' };
    await runInDurableObject(stub(), async (instance, state) => {
      const internal = instance as unknown as { env: Env };
      const directory = internal.env.PUBLIC_DIRECTORY;
      internal.env.PUBLIC_DIRECTORY = undefined;
      try {
        const result = await instance.save(guild, 0, s, person(99).id, 'save');
        expect(result.ok).toBe(true);
        expect(instance.siteMetadata().publication.pending).toBe(true);
        expect(await state.storage.getAlarm()).not.toBeNull();
      } finally {
        internal.env.PUBLIC_DIRECTORY = directory;
      }
      await instance.alarm();
      expect(instance.siteMetadata().publication.pending).toBe(false);
      expect(instance.read(guild).revision).toBe(1);
    });
  });
  it('remembers names without changing the revision or claiming permissions', async () => {
    await stub().read(guild);
    await stub().rememberChannels([{ id: '706320010368516200', name: 'pickups' }]);
    const meta = await stub().siteMetadata();
    expect(meta.knownChannels[0].name).toBe('pickups');
    expect(meta.knownChannels[0].observedAt).toBeGreaterThan(0);
    expect((await stub().read(guild)).revision).toBe(0);
    await stub().rememberChannels([]);
    expect((await stub().siteMetadata()).knownChannels).toHaveLength(1);
  });
  it('summarizes additions rather than walking every default', () => {
    const s = settings();
    expect(changesBetween({ games: [], queues: [] }, s)).toHaveLength(2);
    expect(changesBetween(s, structuredClone(s))).toHaveLength(0);
  });
  it('never renders hidden players, including direct profile queries', async () => {
    const s = settings();
    s.publicPage = { displayName: 'Our group' };
    s.games[0].records = {
      ...defaultRecords,
      visibility: 'public',
      minimumMatches: 0,
      recentDays: 0,
      searchPublishing: true,
    };
    await stub().save(guild, 0, s, person(99).id, 'save');
    await act('add', 0);
    await act('add', 1);
    const meta = await stub().siteMetadata(),
      url = `https://setup.example/c/${meta.publication.community}/${meta.publication.games[0].slug}/`;
    expect(await (await publicPages(new Request(url), env)).text()).toContain('Player 0');
    await stub().recordAction(
      guild,
      { id: crypto.randomUUID(), kind: 'hide', gameId: game, userId: person(0).id },
      person(99).id,
      true,
    );
    const html = await (await publicPages(new Request(url + '?user=' + person(0).id), env)).text();
    expect(html).not.toContain('Player 0');
    expect(html).toContain('Player 1');
  });
  it('keeps channel metadata and publishing settings in operational backup', async () => {
    const s = settings();
    s.publicPage = { displayName: 'A community' };
    await stub().save(guild, 0, s, person(99).id, 'save');
    await stub().rememberChannels([{ id: '706320010368516200', name: 'pickups' }]);
    const backup = await stub().operationalBackup(guild);
    expect(backup.tables.site_metadata).toBeDefined();
    expect(backup.tables.schema_version[0].version).toBe(9);
  });
});

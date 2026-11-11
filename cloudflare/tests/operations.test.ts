import { env, exports } from 'cloudflare:workers';
import {
  createExecutionContext,
  reset,
  runInDurableObject,
  waitOnExecutionContext,
} from 'cloudflare:test';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import app from '../src/worker';
import {
  EXPORT_PAGE_BYTES,
  ORGANIZER_EXCLUDED,
  RESTORE_WINDOW_MS,
  restoreTargetProblem,
  type PointInTime,
} from '../src/storage/community';
import { ACTIVITY_GRANULARITY_MS } from '../src/storage/public-directory';
import { forgetTouchCache } from '../src/storage/community-index';
import { settings, guild, channel, person } from './harness';

const otherGuild = '706320010368516199',
  appId = '100000000000000001',
  userId = '100000000000000002',
  sessionId = 'c'.repeat(64),
  csrf = 'd'.repeat(64),
  base = 'https://setup.example',
  token = 'ops-test-token';
let manager = true;
const directory = () => env.PUBLIC_DIRECTORY!.getByName('directory');
const community = (g = guild) => env.COMMUNITIES.getByName(g);
const cookie = { Cookie: `__Host-cg-session=${sessionId}` };
const write = {
  ...cookie,
  Origin: base,
  'X-CSRF-Token': csrf,
  'Content-Type': 'application/json',
};
async function session() {
  await env.AUTH.getByName(`session:${sessionId}`).saveSession({
    user: { id: userId, username: 'organizer', avatar: null },
    accessToken: 'test-access-token',
    csrf,
    expiresAt: Date.now() + 60000,
  });
}
async function ops(path: string, init: RequestInit = {}) {
  const ctx = createExecutionContext();
  const response = await app.fetch(
    new Request(base + path, {
      ...init,
      headers: { Authorization: `Bearer ${token}`, ...(init.headers ?? {}) },
    }),
    { ...env, OPS_TOKEN: token },
    ctx,
  );
  await waitOnExecutionContext(ctx);
  return response;
}
const post = (path: string, body: unknown) =>
  ops(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

beforeEach(() => {
  manager = true;
  forgetTouchCache();
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = new URL(new Request(input, init).url);
    if (url.pathname === '/api/v10/users/@me/guilds')
      return Response.json(
        [guild, otherGuild].map((id) => ({
          id,
          name: 'Friends',
          owner: false,
          permissions: manager ? '32' : '0',
        })),
      );
    const g = url.pathname.split('/')[4];
    if (url.pathname.endsWith('/channels'))
      return Response.json([
        { id: channel, guild_id: g, name: 'pickup', type: 0, permission_overwrites: [] },
      ]);
    if (url.pathname.endsWith('/roles'))
      return Response.json([{ id: g, name: '@everyone', permissions: '84992' }]);
    if (url.pathname.endsWith(`/members/${appId}`)) return Response.json({ roles: [] });
    return Response.json({ id: '100000000000900000' });
  });
});
afterEach(async () => {
  vi.restoreAllMocks();
  await reset();
});

describe('community index', () => {
  it('records settings and coarse activity, pages newest first and forgets on request', async () => {
    const d = directory(),
      t0 = Date.parse('2026-11-01T00:00:00Z');
    await d.touch(guild, { kind: 'activity' }, t0);
    await d.touch(guild, { kind: 'activity' }, t0 + 60000);
    expect((await d.communities()).communities[0]).toEqual({
      guild,
      firstSeenAt: t0,
      settingsSavedAt: null,
      lastActivityAt: t0,
      hasSettings: false,
    });
    await d.touch(guild, { kind: 'activity' }, t0 + ACTIVITY_GRANULARITY_MS);
    await d.touch(guild, { kind: 'settings', hasSettings: true }, t0 + 2 * ACTIVITY_GRANULARITY_MS);
    await d.touch(otherGuild, { kind: 'activity' }, t0 + 10 * ACTIVITY_GRANULARITY_MS);
    await d.touch('706320010368516197', { kind: 'activity' }, t0 - ACTIVITY_GRANULARITY_MS);
    await d.touch('not-a-guild', { kind: 'activity' }, t0);
    const first = await d.communities(2);
    expect(first.total).toBe(3);
    expect(first.communities.map((c) => c.guild)).toEqual([otherGuild, guild]);
    expect(first.communities[1]).toMatchObject({
      firstSeenAt: t0,
      settingsSavedAt: t0 + 2 * ACTIVITY_GRANULARITY_MS,
      lastActivityAt: t0 + 2 * ACTIVITY_GRANULARITY_MS,
      hasSettings: true,
    });
    const second = await d.communities(2, first.next!);
    expect(second.communities.map((c) => c.guild)).toEqual(['706320010368516197']);
    expect(second.next).toBeNull();
    await d.register({ guild, revision: 1, community: 'friends-x', games: [] });
    await d.forget(guild);
    expect((await d.communities()).communities.map((c) => c.guild)).not.toContain(guild);
    expect(await d.resolve('friends-x')).toBeNull();
  });
  it('registers on a successful console save and on a verified Discord interaction', async () => {
    await session();
    const saved = await exports.default.fetch(`${base}/api/communities/${guild}/config`, {
      method: 'PUT',
      headers: write,
      body: JSON.stringify({ expectedRevision: 0, settings: settings() }),
    });
    expect(saved.status).toBe(200);
    await vi.waitFor(async () =>
      expect((await directory().communities()).communities[0]).toMatchObject({
        guild,
        hasSettings: true,
      }),
    );
    const raw = JSON.stringify({
      type: 2,
      application_id: appId,
      guild_id: otherGuild,
      channel_id: channel,
      member: { permissions: '0' },
      data: { name: 'setup', options: [{ name: 'status' }] },
    });
    const timestamp = String(Math.floor(Date.now() / 1000));
    const key = await crypto.subtle.importKey(
      'pkcs8',
      Uint8Array.from(
        '302e020100300506032b6570042204209d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60'
          .match(/../g)!
          .map((s) => parseInt(s, 16)),
      ),
      'Ed25519',
      false,
      ['sign'],
    );
    const signature = await crypto.subtle.sign(
      'Ed25519',
      key,
      new TextEncoder().encode(timestamp + raw),
    );
    const headers = {
      'Content-Type': 'application/json',
      'X-Signature-Timestamp': timestamp,
      'X-Signature-Ed25519': Array.from(new Uint8Array(signature), (b) =>
        b.toString(16).padStart(2, '0'),
      ).join(''),
    };
    expect(
      (
        await exports.default.fetch(`${base}/interactions`, {
          method: 'POST',
          headers: { ...headers, 'X-Signature-Ed25519': '0'.repeat(128) },
          body: raw,
        })
      ).status,
    ).toBe(401);
    expect(
      (await exports.default.fetch(`${base}/interactions`, { method: 'POST', headers, body: raw }))
        .status,
    ).toBe(200);
    await vi.waitFor(async () =>
      expect((await directory().communities()).communities.map((c) => c.guild)).toEqual(
        expect.arrayContaining([guild, otherGuild]),
      ),
    );
    expect(
      (await directory().communities()).communities.find((c) => c.guild === otherGuild),
    ).toMatchObject({ hasSettings: false, settingsSavedAt: null });
  });
  it('lists communities only for the operator', async () => {
    await directory().touch(guild, { kind: 'activity' });
    expect((await exports.default.fetch(`${base}/ops/communities`)).status).toBe(403);
    const listed = await ops('/ops/communities?limit=10');
    expect(listed.status).toBe(200);
    expect(await listed.json()).toMatchObject({ total: 1, communities: [{ guild }], next: null });
    expect((await ops('/ops/communities?cursor=nonsense')).status).toBe(400);
  });
});

describe('paged export', () => {
  async function addRatingRows(count: number) {
    await runInDurableObject(community(), (_i, state) =>
      state.storage.transactionSync(() => {
        for (let n = 0; n < count; n++)
          state.storage.sql.exec(
            'INSERT INTO rating_changes VALUES (?,?,?,?,?,?,?,?)',
            `change-${n}`,
            settings().games[0].id,
            person(n % 8).id,
            null,
            1000,
            1010,
            n,
            person(99).id,
          );
      }),
    );
  }
  it('pages one table by rowid cursor within row and byte bounds', async () => {
    await community().save(guild, 0, settings(), person(99).id, 'save');
    await addRatingRows(25);
    const manifest = await community().exportManifest(guild);
    expect(manifest.tables.find((t) => t.name === 'rating_changes')?.rows).toBe(25);
    expect(manifest.tables.some((t) => t.name.startsWith('_'))).toBe(false);
    const seen: string[] = [];
    for (let after: number | null = 0; after !== null;) {
      const page = await community().exportPage(guild, 'rating_changes', after, 10);
      expect(page.rows.length).toBeLessThanOrEqual(10);
      seen.push(...page.rows.map((r) => String(r.id)));
      after = page.next;
    }
    expect(seen).toEqual(Array.from({ length: 25 }, (_, n) => `change-${n}`));
    await runInDurableObject(community(), (_i, state) => {
      for (let n = 0; n < 3; n++)
        state.storage.sql.exec(
          'INSERT INTO site_metadata VALUES (?,?)',
          `large-${n}`,
          JSON.stringify('x'.repeat(EXPORT_PAGE_BYTES * 0.6)),
        );
    });
    const pages = [];
    for (let after: number | null = 0; after !== null;) {
      const page = await community().exportPage(guild, 'site_metadata', after);
      pages.push(page.rows.length);
      after = page.next;
    }
    expect(pages.filter(Boolean).length).toBeGreaterThanOrEqual(3);
    for (const attempt of [
      () => community().exportPage(guild, 'sqlite_master'),
      () => community().exportPage(guild, 'outbox', 0, 10, 'organizer'),
    ])
      expect(
        String(
          await attempt().then(
            () => 'accepted',
            (e: Error) => e.message,
          ),
        ),
      ).toContain('Unknown export table');
  });
  it('backs up more than 50,000 rows in one table and keeps the backup artifact shape', async () => {
    await community().save(guild, 0, settings(), person(99).id, 'save');
    await addRatingRows(50050);
    const response = await ops(`/ops/communities/${guild}/backup`);
    expect(response.status).toBe(200);
    const backup = (await response.json()) as {
      tables: Record<string, unknown[]>;
      [key: string]: unknown;
    };
    expect(Object.keys(backup)).toEqual([
      'format',
      'communityId',
      'sourceApplicationId',
      'createdAt',
      'tables',
    ]);
    expect(backup.tables.rating_changes).toHaveLength(50050);
    expect(backup.tables.community).toEqual([{ id: 1, guild_id: guild, revision: 1 }]);
    const manifest = await (await ops(`/ops/communities/${guild}/export`)).json();
    expect(manifest).toMatchObject({ communityId: guild, scope: 'operator' });
    const page = await ops(`/ops/communities/${guild}/export/rating_changes?after=50000`);
    expect(await page.json()).toMatchObject({ next: null });
    expect((await ops(`/ops/communities/${guild}/export/nonexistent`)).status).toBe(404);
    expect((await community().operationalBackup(guild)).tables.rating_changes).toHaveLength(50050);
  });
});

describe('organizer data download', () => {
  it('requires a signed-in manager and downloads only this community’s records', async () => {
    const path = `/api/communities/${guild}/export`;
    expect((await exports.default.fetch(base + path)).status).toBe(401);
    await session();
    manager = false;
    expect((await exports.default.fetch(base + path, { headers: cookie })).status).toBe(403);
    manager = true;
    await community().save(guild, 0, settings(), person(99).id, 'save');
    await community(otherGuild).save(
      otherGuild,
      0,
      { ...settings(), publicPage: { displayName: 'Secret neighbours' } },
      person(98).id,
      'save',
    );
    const response = await exports.default.fetch(base + path, { headers: cookie });
    expect(response.status).toBe(200);
    expect(response.headers.get('Content-Disposition')).toMatch(
      new RegExp(`^attachment; filename="community-${guild}-\\d{4}-\\d\\d-\\d\\d\\.json"$`),
    );
    expect(response.headers.get('Cache-Control')).toBe('no-store');
    const text = await response.text();
    expect(text).not.toContain(otherGuild);
    expect(text).not.toContain('Secret neighbours');
    expect(text).not.toContain(person(98).id);
    const file = JSON.parse(text);
    expect(file).toMatchObject({
      format: 1,
      kind: 'community-data-export',
      communityId: guild,
      manifest: { schemaVersion: 9, excluded: ORGANIZER_EXCLUDED },
    });
    expect(file).not.toHaveProperty('sourceApplicationId');
    for (const name of ORGANIZER_EXCLUDED) expect(file.tables).not.toHaveProperty(name);
    for (const { name, rows } of file.manifest.tables) expect(file.tables[name]).toHaveLength(rows);
    expect(file.tables.queues).toHaveLength(1);
    expect(file.tables.settings_audit[0]).toMatchObject({ actor_id: person(99).id });
  });
});

describe('point-in-time restore', () => {
  it('validates the restore target', () => {
    const now = Date.parse('2026-11-11T12:00:00Z');
    expect(restoreTargetProblem({}, now)).toMatch(/exactly one/);
    expect(restoreTargetProblem({ time: now, bookmark: 'x' }, now)).toMatch(/exactly one/);
    expect(restoreTargetProblem({ time: now + 1 }, now)).toMatch(/future/);
    expect(restoreTargetProblem({ time: now - RESTORE_WINDOW_MS - 1 }, now)).toMatch(/30-day/);
    expect(restoreTargetProblem({ time: now - RESTORE_WINDOW_MS + 60000 }, now)).toBeNull();
    expect(restoreTargetProblem({ bookmark: 'not a bookmark' }, now)).toMatch(/format/);
    expect(
      restoreTargetProblem(
        { bookmark: '0000007b-0000b26e-00001538-0c3e87bb37b3db5cc52eedb93cd3b96b' },
        now,
      ),
    ).toBeNull();
  });
  it('requires the operator, confirmation and an existing community', async () => {
    const path = `/ops/communities/${guild}/restore-to`;
    const time = new Date(Date.now() - 3600000).toISOString();
    expect(
      (
        await exports.default.fetch(base + path, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ time, confirm: guild }),
        })
      ).status,
    ).toBe(403);
    expect((await post(path, { time, confirm: otherGuild })).status).toBe(400);
    expect((await post(path, { time: 'yesterday', confirm: guild })).status).toBe(400);
    const missing = await post(path, { time, confirm: guild });
    expect(missing.status).toBe(409);
    expect(await missing.json()).toMatchObject({ error: expect.stringContaining('No community') });
    await community().save(guild, 0, settings(), person(99).id, 'save');
    const future = await post(path, {
      time: new Date(Date.now() + 3600000).toISOString(),
      confirm: guild,
    });
    expect(await future.json()).toMatchObject({ error: expect.stringContaining('future') });
    // The local workerd storage has no point-in-time recovery; production does.
    const local = await post(path, { time, confirm: guild });
    expect(local.status).toBe(409);
    expect(await local.json()).toMatchObject({
      error: expect.stringContaining('point-in-time recovery'),
    });
  });
  it('arms the restore, records an operator note with the undo bookmark and restarts the object', async () => {
    await community().save(guild, 0, settings(), person(99).id, 'save');
    const calls: string[] = [];
    await runInDurableObject(community(), (instance, state) => {
      const fake: PointInTime = {
        current: async () => '00000001-00000000-00000000-' + 'a'.repeat(32),
        forTime: async (at) => {
          calls.push(`forTime:${at}`);
          return '00000002-00000000-00000000-' + 'b'.repeat(32);
        },
        arm: async (bookmark) => {
          calls.push(`arm:${bookmark}`);
          return '00000003-00000000-00000000-' + 'c'.repeat(32);
        },
        restart: (reason) => {
          calls.push('restart');
          state.abort(reason);
        },
      };
      Object.assign(instance, { pointInTime: fake, marker: true });
    });
    const time = new Date(Date.now() - 86400000).toISOString();
    const response = await post(`/ops/communities/${guild}/restore-to`, {
      time,
      confirm: guild,
      reason: 'Accidental queue deletion',
    });
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      ok: true,
      communityId: guild,
      restoredTo: '00000002-00000000-00000000-' + 'b'.repeat(32),
      undoBookmark: '00000003-00000000-00000000-' + 'c'.repeat(32),
      noted: true,
      undo: { bookmark: '00000003-00000000-00000000-' + 'c'.repeat(32), confirm: guild },
    });
    expect(calls).toEqual([
      `forTime:${Date.parse(time)}`,
      'arm:00000002-00000000-00000000-' + 'b'.repeat(32),
      'restart',
    ]);
    expect((await directory().notes(guild))[0]).toMatchObject({
      kind: 'restore_to',
      details: {
        requested: time,
        undoBookmark: '00000003-00000000-00000000-' + 'c'.repeat(32),
        previousBookmark: '00000001-00000000-00000000-' + 'a'.repeat(32),
        reason: 'Accidental queue deletion',
      },
    });
    const notes = await ops(`/ops/communities/${guild}/notes`);
    expect(((await notes.json()) as { notes: unknown[] }).notes).toHaveLength(1);
    // A fresh instance replaced the aborted one, so the fake seam is gone.
    await runInDurableObject(community(), (instance) =>
      expect((instance as unknown as { marker?: boolean }).marker).toBeUndefined(),
    );
    expect((await community().read(guild)).revision).toBe(1);
  });
});

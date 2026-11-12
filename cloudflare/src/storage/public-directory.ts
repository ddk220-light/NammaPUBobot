import { DurableObject } from 'cloudflare:workers';
import type { Env } from '../env';
type Entry = {
  guild: string;
  revision: number;
  community: string;
  games: { id: string; slug: string }[];
};
export type CommunityIndexRow = {
  guild: string;
  firstSeenAt: number;
  settingsSavedAt: number | null;
  lastActivityAt: number;
  hasSettings: boolean;
};
export type CommunityTouch = { kind: 'activity' } | { kind: 'settings'; hasSettings: boolean };
/** Activity is coarse on purpose: one write per community per hour at most. */
export const ACTIVITY_GRANULARITY_MS = 3600000;
/** Directory entries are hints. Public requests always recheck saved community policy. */
export class PublicDirectory extends DurableObject<Env> {
  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    const sql = ctx.storage.sql;
    sql.exec('CREATE TABLE IF NOT EXISTS pages (guild TEXT PRIMARY KEY, data TEXT NOT NULL)');
    // Operator tenant index: which communities exist. Lives here so it needs no new class or migration.
    sql.exec(
      'CREATE TABLE IF NOT EXISTS communities (guild TEXT PRIMARY KEY, first_seen_at INTEGER NOT NULL, settings_saved_at INTEGER, last_activity_at INTEGER NOT NULL, has_settings INTEGER NOT NULL DEFAULT 0)',
    );
    sql.exec(
      'CREATE INDEX IF NOT EXISTS communities_activity ON communities(last_activity_at DESC, guild DESC)',
    );
    // Kept outside community storage so a point-in-time restore cannot rewind its own audit trail.
    sql.exec(
      'CREATE TABLE IF NOT EXISTS ops_notes (id INTEGER PRIMARY KEY AUTOINCREMENT, guild TEXT NOT NULL, kind TEXT NOT NULL, at INTEGER NOT NULL, details TEXT NOT NULL)',
    );
  }
  register(entry: Entry) {
    this.ctx.storage.sql.exec(
      `INSERT INTO pages VALUES (?,?) ON CONFLICT(guild) DO UPDATE SET data=excluded.data WHERE COALESCE(json_extract(pages.data, '$.revision'),0) <= json_extract(excluded.data, '$.revision')`,
      entry.guild,
      JSON.stringify(entry),
    );
  }
  entries() {
    return this.ctx.storage.sql
      .exec<{ data: string }>('SELECT data FROM pages ORDER BY guild')
      .toArray()
      .map((r) => JSON.parse(r.data) as Entry);
  }
  resolve(community: string) {
    return this.entries().find((e) => e.community === community) ?? null;
  }
  touch(guild: string, touch: CommunityTouch, at = Date.now()) {
    if (!/^\d{17,20}$/.test(guild)) return;
    const sql = this.ctx.storage.sql;
    if (touch.kind === 'settings')
      sql.exec(
        'INSERT INTO communities VALUES (?,?,?,?,?) ON CONFLICT(guild) DO UPDATE SET settings_saved_at=excluded.settings_saved_at, last_activity_at=MAX(communities.last_activity_at, excluded.last_activity_at), has_settings=excluded.has_settings',
        guild,
        at,
        at,
        at,
        touch.hasSettings ? 1 : 0,
      );
    else
      sql.exec(
        'INSERT INTO communities(guild,first_seen_at,last_activity_at) VALUES (?,?,?) ON CONFLICT(guild) DO UPDATE SET last_activity_at=excluded.last_activity_at WHERE excluded.last_activity_at >= communities.last_activity_at + ?',
        guild,
        at,
        at,
        ACTIVITY_GRANULARITY_MS,
      );
  }
  /** Newest activity first; `cursor` is the previous page's `next`. */
  communities(limit = 100, cursor?: string) {
    const size = Math.min(Math.max(Math.trunc(limit) || 100, 1), 500);
    const [at, guild] = /^\d+:\d{17,20}$/.test(cursor ?? '') ? cursor!.split(':') : [];
    const rows = this.ctx.storage.sql
      .exec<{
        guild: string;
        first_seen_at: number;
        settings_saved_at: number | null;
        last_activity_at: number;
        has_settings: number;
      }>(
        `SELECT * FROM communities ${at ? 'WHERE (last_activity_at, guild) < (?, ?)' : ''} ORDER BY last_activity_at DESC, guild DESC LIMIT ?`,
        ...(at ? [Number(at), guild] : []),
        size + 1,
      )
      .toArray();
    const page = rows.slice(0, size).map((r): CommunityIndexRow => ({
      guild: r.guild,
      firstSeenAt: r.first_seen_at,
      settingsSavedAt: r.settings_saved_at,
      lastActivityAt: r.last_activity_at,
      hasSettings: !!r.has_settings,
    }));
    const last = page.at(-1);
    return {
      communities: page,
      total: this.ctx.storage.sql.exec<{ n: number }>('SELECT COUNT(*) AS n FROM communities').one()
        .n,
      next: rows.length > size && last ? `${last.lastActivityAt}:${last.guild}` : null,
    };
  }
  /** For the delete-community path: drops the index row and any public page hint. Ops notes stay. */
  forget(guild: string) {
    const sql = this.ctx.storage.sql;
    sql.exec('DELETE FROM communities WHERE guild=?', guild);
    sql.exec('DELETE FROM pages WHERE guild=?', guild);
  }
  note(guild: string, kind: string, details: Record<string, unknown>, at = Date.now()) {
    this.ctx.storage.sql.exec(
      'INSERT INTO ops_notes(guild,kind,at,details) VALUES (?,?,?,?)',
      guild,
      kind,
      at,
      JSON.stringify(details),
    );
  }
  notes(guild: string, limit = 50) {
    return this.ctx.storage.sql
      .exec<{ id: number; kind: string; at: number; details: string }>(
        'SELECT id,kind,at,details FROM ops_notes WHERE guild=? ORDER BY id DESC LIMIT ?',
        guild,
        Math.min(Math.max(limit, 1), 200),
      )
      .toArray()
      .map((r) => ({ ...r, details: JSON.parse(r.details) }));
  }
}

import type { Queue } from '../domain/config';

export function createUxTables(sql: SqlStorage) {
  sql.exec(
    `CREATE TABLE IF NOT EXISTS readiness(queue_id TEXT NOT NULL,game_id TEXT NOT NULL,user_id TEXT NOT NULL,confirmed_at INTEGER NOT NULL,expires_at INTEGER NOT NULL,PRIMARY KEY(queue_id,game_id,user_id))`,
  );
  sql.exec(
    `CREATE TABLE IF NOT EXISTS queue_runtime(queue_id TEXT PRIMARY KEY,roster_revision INTEGER NOT NULL DEFAULT 0,hold INTEGER NOT NULL DEFAULT 0)`,
  );
  sql.exec(
    `CREATE TABLE IF NOT EXISTS pending_returns(seq INTEGER PRIMARY KEY AUTOINCREMENT,match_id TEXT NOT NULL,queue_id TEXT NOT NULL,user_id TEXT NOT NULL,name TEXT NOT NULL,due INTEGER NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,status TEXT NOT NULL DEFAULT 'pending',UNIQUE(match_id,user_id))`,
  );
  sql.exec(`CREATE INDEX IF NOT EXISTS return_due ON pending_returns(status,due)`);
  sql.exec(
    `CREATE TABLE IF NOT EXISTS queue_views(queue_id TEXT PRIMARY KEY,digest TEXT NOT NULL,generation INTEGER NOT NULL,current_generation INTEGER NOT NULL DEFAULT 0)`,
  );
  sql.exec(
    `CREATE TABLE IF NOT EXISTS queue_publications(key TEXT PRIMARY KEY,queue_id TEXT NOT NULL,generation INTEGER NOT NULL,state TEXT NOT NULL DEFAULT 'pending',UNIQUE(queue_id,generation))`,
  );
  if (
    !sql
      .exec<{ name: string }>('PRAGMA table_info(queue_publications)')
      .toArray()
      .some((column) => column.name === 'visibility_only')
  )
    sql.exec(
      'ALTER TABLE queue_publications ADD COLUMN visibility_only INTEGER NOT NULL DEFAULT 0',
    );
  if (
    !sql
      .exec<{ name: string }>('PRAGMA table_info(queue_publications)')
      .toArray()
      .some((column) => column.name === 'after_matches')
  )
    sql.exec("ALTER TABLE queue_publications ADD COLUMN after_matches TEXT NOT NULL DEFAULT '[]'");
  sql.exec(
    `CREATE TABLE IF NOT EXISTS rally_runs(id TEXT PRIMARY KEY,queue_id TEXT NOT NULL,created_at INTEGER NOT NULL,targets TEXT NOT NULL,status TEXT NOT NULL)`,
  );
}

export class UxStore {
  constructor(private sql: SqlStorage) {}
  ready(q: Queue, user: string, now: number) {
    const row = this.sql
      .exec<{ confirmed_at: number; expires_at: number }>(
        'SELECT confirmed_at,expires_at FROM readiness WHERE queue_id=? AND game_id=? AND user_id=?',
        q.id,
        q.gameId,
        user,
      )
      .toArray()[0];
    return (
      !!row &&
      now < Math.min(row.expires_at, row.confirmed_at + (q.checkIn.reuseSeconds ?? 900) * 1000)
    );
  }
  confirm(q: Queue, user: string, now: number) {
    this.sql.exec(
      'INSERT INTO readiness VALUES(?,?,?,?,?) ON CONFLICT(queue_id,game_id,user_id) DO UPDATE SET confirmed_at=excluded.confirmed_at,expires_at=excluded.expires_at',
      q.id,
      q.gameId,
      user,
      now,
      now + (q.checkIn.reuseSeconds ?? 900) * 1000,
    );
  }
  clear(user: string, queue?: string) {
    this.sql.exec(
      `DELETE FROM readiness WHERE user_id=?${queue ? ' AND queue_id=?' : ''}`,
      user,
      ...(queue ? [queue] : []),
    );
  }
  leave(user: string, queue?: string) {
    this.clear(user, queue);
    this.sql.exec(
      `DELETE FROM pending_returns WHERE user_id=?${queue ? ' AND queue_id=?' : ''}`,
      user,
      ...(queue ? [queue] : []),
    );
  }
  hold(queue: string, value: boolean) {
    this.sql.exec(
      'INSERT INTO queue_runtime(queue_id,hold) VALUES(?,?) ON CONFLICT(queue_id) DO UPDATE SET hold=excluded.hold',
      queue,
      value ? 1 : 0,
    );
  }
  held(queue: string) {
    return (
      this.sql
        .exec<{ hold: number }>('SELECT hold FROM queue_runtime WHERE queue_id=?', queue)
        .toArray()[0]?.hold === 1
    );
  }
}

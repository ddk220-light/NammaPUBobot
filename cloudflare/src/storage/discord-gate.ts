import { DurableObject } from 'cloudflare:workers';
import type { Env } from '../env';

// Shared token limits across communities, with no timer or always-running service.
export class DiscordGate extends DurableObject<Env> {
  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    ctx.storage.sql.exec(
      'CREATE TABLE IF NOT EXISTS limits (route TEXT PRIMARY KEY, until_ms INTEGER NOT NULL)',
    );
    ctx.storage.sql.exec(
      'CREATE TABLE IF NOT EXISTS budget (id INTEGER PRIMARY KEY, window_ms INTEGER NOT NULL, used INTEGER NOT NULL)',
    );
  }
  reserve(route: string) {
    return this.ctx.storage.transactionSync(() => {
      const now = Date.now(),
        sql = this.ctx.storage.sql;
      const pause =
        sql
          .exec<{ until_ms: number }>(
            'SELECT MAX(until_ms) AS until_ms FROM limits WHERE route IN (?, ?)',
            route,
            '*',
          )
          .toArray()[0]?.until_ms ?? 0;
      if (pause > now) return pause;
      const tick = Math.floor(now / 1000) * 1000;
      const budget = sql
        .exec<{ window_ms: number; used: number }>('SELECT * FROM budget WHERE id=1')
        .toArray()[0];
      if (budget?.window_ms === tick && budget.used >= 40) return tick + 1000;
      sql.exec(
        'INSERT INTO budget VALUES(1, ?, 1) ON CONFLICT(id) DO UPDATE SET used=CASE WHEN window_ms=excluded.window_ms THEN used+1 ELSE 1 END, window_ms=excluded.window_ms',
        tick,
      );
      return now;
    });
  }
  pause(route: string, until: number, global = false) {
    this.ctx.storage.sql.exec(
      'INSERT INTO limits VALUES(?, ?) ON CONFLICT(route) DO UPDATE SET until_ms=MAX(until_ms, excluded.until_ms)',
      global ? '*' : route,
      until,
    );
    this.ctx.storage.sql.exec('DELETE FROM limits WHERE until_ms<?', Date.now() - 60000);
  }
}

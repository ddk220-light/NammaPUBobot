import { DurableObject } from 'cloudflare:workers';
import type { Env } from '../env';
type Entry = {
  guild: string;
  revision: number;
  community: string;
  games: { id: string; slug: string }[];
};
/** Directory entries are hints. Public requests always recheck saved community policy. */
export class PublicDirectory extends DurableObject<Env> {
  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    ctx.storage.sql.exec(
      'CREATE TABLE IF NOT EXISTS pages (guild TEXT PRIMARY KEY, data TEXT NOT NULL)',
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
}

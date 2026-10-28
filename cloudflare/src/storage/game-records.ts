import { gameAdapter } from '../games/registry';
import type { Match } from '../domain/play';
import type { LobbyObservation } from '../games/types';
import { inferLinks, type IdentityPlayer } from '../domain/identity';
export function createGameRecordTables(sql: SqlStorage) {
  sql.exec(
    'CREATE TABLE IF NOT EXISTS identity_rejections(game_id TEXT NOT NULL,provider TEXT NOT NULL,external_id TEXT NOT NULL,user_id TEXT NOT NULL,at INTEGER NOT NULL,PRIMARY KEY(game_id,provider,external_id,user_id))',
  );
  sql.exec(
    'CREATE TABLE IF NOT EXISTS played_selections(match_id TEXT NOT NULL,game_id TEXT NOT NULL,user_id TEXT NOT NULL,provider TEXT NOT NULL,external_id TEXT NOT NULL,selection TEXT NOT NULL,side INTEGER NOT NULL,provenance TEXT NOT NULL,source TEXT NOT NULL,at INTEGER NOT NULL,PRIMARY KEY(match_id,user_id))',
  );
  sql.exec('CREATE INDEX IF NOT EXISTS selection_player ON played_selections(game_id,user_id)');
}
export function observeGamePlayers(
  sql: SqlStorage,
  m: Match,
  provider: string,
  o: LobbyObservation,
) {
  if (m.test || !o.startedAt) return [];
  const refs = sql
    .exec<{ user_id: string; external_id: string; provenance: string }>(
      'SELECT user_id,external_id,provenance FROM profile_refs WHERE game_id=? AND provider=?',
      m.queue.gameId,
      provider,
    )
    .toArray();
  const roster: IdentityPlayer[] = m.teams.flatMap((team, side) =>
    team.map((p) => {
      const links = refs.filter((r) => r.user_id === p.id);
      return {
        userId: p.id,
        name: p.name,
        side,
        profileId: links.length === 1 ? links[0].external_id : null,
        provenance: links[0]?.provenance ?? '',
      };
    }),
  );
  const adapter = gameAdapter(m.lobby?.adapterId ?? provider);
  const inferred = (adapter?.profileProvider === provider ? inferLinks(roster, o) : []).filter(
    (link) =>
      !refs.some((r) => r.user_id === link.userId || r.external_id === link.profileId) &&
      !sql
        .exec(
          'SELECT 1 FROM identity_rejections WHERE game_id=? AND provider=? AND external_id=? AND user_id=?',
          m.queue.gameId,
          provider,
          link.profileId,
          link.userId,
        )
        .toArray().length,
  );
  for (const link of inferred) {
    const provenance = `inferred:${link.method}:${link.confidence.toFixed(2)}:${m.id}`;
    // Public profile URLs come from the same registered adapter as the identity evidence.
    sql.exec(
      'INSERT INTO profile_refs VALUES(?,?,?,?,?,?,?)',
      m.queue.gameId,
      provider,
      link.profileId,
      link.userId,
      adapter!.profileUrl(link.profileId),
      provenance,
      Date.now(),
    );
    const p = roster.find((p) => p.userId === link.userId)!;
    p.profileId = link.profileId;
    p.provenance = provenance;
  }
  for (const p of roster) {
    if (!p.profileId) continue;
    const actual = o.players.find((x) => x.profileId === p.profileId);
    if (!actual?.selection) continue;
    // Attribution is by linked game ID, never array position or the suggested picker choice.
    sql.exec(
      'INSERT INTO played_selections VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(match_id,user_id) DO UPDATE SET provider=excluded.provider,external_id=excluded.external_id,selection=excluded.selection,side=excluded.side,provenance=excluded.provenance,source=excluded.source,at=excluded.at',
      m.id,
      m.queue.gameId,
      p.userId,
      provider,
      p.profileId,
      actual.selection,
      p.side,
      p.provenance,
      o.source,
      o.observedAt,
    );
  }
  return inferred;
}
export function selectionStats(sql: SqlStorage, game: string, user: string) {
  return sql
    .exec<{
      selection: string;
      games: number;
      wins: number;
      losses: number;
      draws: number;
      inferred: number;
    }>(
      `SELECT s.selection,COUNT(*) AS games,SUM(CASE WHEN json_extract(m.config,'$.result')=CASE s.side WHEN 0 THEN 'alpha' ELSE 'beta' END THEN 1 ELSE 0 END) AS wins,SUM(CASE WHEN json_extract(m.config,'$.result')=CASE s.side WHEN 0 THEN 'beta' ELSE 'alpha' END THEN 1 ELSE 0 END) AS losses,SUM(CASE WHEN json_extract(m.config,'$.result')='draw' THEN 1 ELSE 0 END) AS draws,SUM(CASE WHEN s.provenance LIKE 'inferred:%' THEN 1 ELSE 0 END) AS inferred FROM played_selections s JOIN matches m ON m.id=s.match_id JOIN profile_refs r ON r.game_id=s.game_id AND r.provider=s.provider AND r.external_id=s.external_id AND r.user_id=s.user_id WHERE s.game_id=? AND s.user_id=? AND m.state='finished' AND json_extract(m.config,'$.test') IS NULL AND EXISTS (SELECT 1 FROM json_each(m.config, '$.teams[' || s.side || ']') p WHERE json_extract(p.value,'$.id')=s.user_id) GROUP BY s.selection ORDER BY games DESC,s.selection`,
      game,
      user,
    )
    .toArray();
}

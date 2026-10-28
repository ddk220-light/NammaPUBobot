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
  if (m.test || !['started', 'finished'].includes(o.phase)) return [];
  const adapter = gameAdapter(m.lobby?.adapterId ?? provider);
  const refs = sql
    .exec<{
      user_id: string;
      external_id: string;
      provenance: string;
      edition: string | null;
      namespace: string | null;
    }>(
      'SELECT r.user_id,r.external_id,r.provenance,c.edition,c.namespace FROM profile_refs r LEFT JOIN profile_claims c ON c.game_id=r.game_id AND c.provider=r.provider AND c.external_id=r.external_id AND c.user_id=r.user_id WHERE r.game_id=? AND r.provider=?',
      m.queue.gameId,
      provider,
    )
    .toArray();
  const roster: IdentityPlayer[] = m.teams.flatMap((team, side) =>
    team.map((p) => {
      const links = refs.filter(
        (r) =>
          r.user_id === p.id &&
          !r.provenance.startsWith('inferred:') &&
          !!adapter &&
          ((r.edition === adapter.manifest.edition &&
            r.namespace === adapter.manifest.accountNamespace) ||
            (adapter.id === 'aoe2' &&
              provider === 'aoe2' &&
              r.edition === null &&
              r.namespace === null)),
      );
      return {
        userId: p.id,
        name: p.name,
        side,
        profileId: links.length === 1 ? links[0].external_id : null,
        provenance: links[0]?.provenance ?? '',
      };
    }),
  );
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
  // A matching name or remaining team slot is only a suggestion, never a persisted link.
  if (!m.lobby?.rosterVerified && !m.lobby?.confirmedBy) return inferred;
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
  const observed = sql
    .exec<{
      selection: string;
      games: number;
      wins: number;
      losses: number;
      draws: number;
      inferred: number;
    }>(
      `SELECT s.selection,COUNT(*) AS games,SUM(CASE WHEN json_extract(m.config,'$.result')=CASE s.side WHEN 0 THEN 'alpha' ELSE 'beta' END THEN 1 ELSE 0 END) AS wins,SUM(CASE WHEN json_extract(m.config,'$.result')=CASE s.side WHEN 0 THEN 'beta' ELSE 'alpha' END THEN 1 ELSE 0 END) AS losses,SUM(CASE WHEN json_extract(m.config,'$.result')='draw' THEN 1 ELSE 0 END) AS draws,SUM(CASE WHEN s.provenance LIKE 'inferred:%' THEN 1 ELSE 0 END) AS inferred FROM played_selections s JOIN matches m ON m.id=s.match_id JOIN profile_refs r ON r.game_id=s.game_id AND r.provider=s.provider AND r.external_id=s.external_id AND r.user_id=s.user_id WHERE s.game_id=? AND s.user_id=? AND m.state='finished' AND r.provenance NOT LIKE 'inferred:%' AND s.provenance NOT LIKE 'inferred:%' AND json_extract(m.config,'$.test') IS NULL AND EXISTS (SELECT 1 FROM json_each(m.config, '$.teams[' || s.side || ']') p WHERE json_extract(p.value,'$.id')=s.user_id) GROUP BY s.selection ORDER BY games DESC,s.selection`,
      game,
      user,
    )
    .toArray();
  // Aggregate declared facts in SQLite; never fabricate a provider account to retain them.
  const manual = sql
    .exec<(typeof observed)[number]>(
      `SELECT json_extract(f.value,'$.selection') AS selection,COUNT(*) AS games,
      SUM(CASE WHEN json_extract(m.config,'$.result')=CASE json_extract(f.value,'$.side') WHEN 0 THEN 'alpha' ELSE 'beta' END THEN 1 ELSE 0 END) AS wins,
      SUM(CASE WHEN json_extract(m.config,'$.result')=CASE json_extract(f.value,'$.side') WHEN 0 THEN 'beta' ELSE 'alpha' END THEN 1 ELSE 0 END) AS losses,
      SUM(CASE WHEN json_extract(m.config,'$.result')='draw' THEN 1 ELSE 0 END) AS draws,0 AS inferred
      FROM matches m,json_each(m.config,'$.lobby.manualFacts.players') f
      WHERE m.game_id=? AND m.state='finished' AND json_extract(m.config,'$.test') IS NULL
      AND json_extract(m.config,'$.result') IN ('alpha','beta','draw')
      AND json_type(m.config,'$.lobby.confirmedBy')='text'
      AND json_extract(f.value,'$.userId')=? AND json_extract(f.value,'$.side') IN (0,1)
      AND EXISTS(SELECT 1 FROM json_each(m.config,'$.teams[' || json_extract(f.value,'$.side') || ']') p WHERE json_extract(p.value,'$.id')=?)
      AND NOT EXISTS(SELECT 1 FROM played_selections s JOIN profile_refs r ON r.game_id=s.game_id AND r.provider=s.provider AND r.external_id=s.external_id AND r.user_id=s.user_id WHERE s.match_id=m.id AND s.user_id=? AND r.provenance NOT LIKE 'inferred:%' AND s.provenance NOT LIKE 'inferred:%')
      GROUP BY selection`,
      game,
      user,
      user,
      user,
    )
    .toArray();
  const merged = new Map(observed.map((row) => [row.selection, row]));
  for (const row of manual) {
    const prior = merged.get(row.selection);
    merged.set(
      row.selection,
      prior
        ? {
            ...prior,
            games: prior.games + row.games,
            wins: prior.wins + row.wins,
            losses: prior.losses + row.losses,
            draws: prior.draws + row.draws,
          }
        : row,
    );
  }
  return [...merged.values()].sort(
    (a, b) => b.games - a.games || a.selection.localeCompare(b.selection),
  );
}

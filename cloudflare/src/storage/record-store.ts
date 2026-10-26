import type { Env } from '../env';
import type { Snapshot } from '../domain/config';
import { PlayError, policy, type Match } from '../domain/play';
import type { Player } from '../domain/rating';
import { privilegedRankPermissions } from '../domain/rank-roles';
import {
  canonical,
  digest,
  importSchema,
  recordActionSchema,
  recordPolicy,
  rankLabel,
  type ImportBundle,
  type RecordAction,
} from '../domain/records';
import type {
  Statistics,
  PlayerStatistics,
  RatingHistory,
  ProfileReference,
} from '../domain/records';

export function createRecordTables(sql: SqlStorage) {
  sql.exec(
    'CREATE TABLE IF NOT EXISTS record_state(id INTEGER PRIMARY KEY,epoch INTEGER NOT NULL)',
  );
  sql.exec('INSERT OR IGNORE INTO record_state VALUES(1,0)');
  sql.exec(
    "CREATE TABLE IF NOT EXISTS player_meta(game_id TEXT NOT NULL,user_id TEXT NOT NULL,hidden INTEGER NOT NULL DEFAULT 0,last_ranked INTEGER,source TEXT NOT NULL DEFAULT 'local',PRIMARY KEY(game_id,user_id))",
  );
  sql.exec(
    'CREATE TABLE IF NOT EXISTS participants(game_id TEXT NOT NULL,user_id TEXT NOT NULL,match_id TEXT NOT NULL,at INTEGER NOT NULL,state TEXT NOT NULL,PRIMARY KEY(match_id,user_id))',
  );
  sql.exec(
    'CREATE INDEX IF NOT EXISTS participant_player ON participants(game_id,user_id,at DESC)',
  );
  sql.exec(
    'CREATE TABLE IF NOT EXISTS player_baselines(game_id TEXT NOT NULL,user_id TEXT NOT NULL,config TEXT NOT NULL,last_ranked INTEGER,PRIMARY KEY(game_id,user_id))',
  );
  sql.exec(
    'CREATE TABLE IF NOT EXISTS retired_ranks(game_id TEXT NOT NULL,user_id TEXT NOT NULL,role_id TEXT NOT NULL,PRIMARY KEY(game_id,user_id,role_id))',
  );
  sql.exec(
    'CREATE TABLE IF NOT EXISTS profile_refs(game_id TEXT NOT NULL,provider TEXT NOT NULL,external_id TEXT NOT NULL,user_id TEXT NOT NULL,url TEXT NOT NULL,provenance TEXT NOT NULL,at INTEGER NOT NULL,PRIMARY KEY(game_id,provider,external_id))',
  );
  sql.exec('CREATE INDEX IF NOT EXISTS refs_player ON profile_refs(game_id,user_id)');
  sql.exec(
    'CREATE TABLE IF NOT EXISTS record_audit(id TEXT PRIMARY KEY,game_id TEXT NOT NULL,user_id TEXT,kind TEXT NOT NULL,actor TEXT NOT NULL,at INTEGER NOT NULL,details TEXT NOT NULL)',
  );
  sql.exec(
    'CREATE TABLE IF NOT EXISTS record_actions(id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,result TEXT NOT NULL,at INTEGER NOT NULL)',
  );
  sql.exec(
    'CREATE TABLE IF NOT EXISTS imports(id TEXT PRIMARY KEY,source TEXT NOT NULL,at INTEGER NOT NULL,actor TEXT NOT NULL,summary TEXT NOT NULL)',
  );
  sql.exec(
    'CREATE TABLE IF NOT EXISTS import_sources(source TEXT NOT NULL,kind TEXT NOT NULL,source_id TEXT NOT NULL,local_id TEXT NOT NULL,PRIMARY KEY(source,kind,source_id))',
  );
  sql.exec(
    'CREATE TABLE IF NOT EXISTS rating_details(change_id TEXT PRIMARY KEY,deviation_before INTEGER NOT NULL,deviation_after INTEGER NOT NULL,reason TEXT NOT NULL,source TEXT NOT NULL)',
  );
  sql.exec(
    "CREATE TABLE IF NOT EXISTS rank_jobs(game_id TEXT NOT NULL,user_id TEXT NOT NULL,version INTEGER NOT NULL,due INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'pending',attempts INTEGER NOT NULL DEFAULT 0,error TEXT,PRIMARY KEY(game_id,user_id))",
  );
  sql.exec(
    'CREATE TABLE IF NOT EXISTS record_clocks(game_id TEXT PRIMARY KEY,due INTEGER NOT NULL)',
  );
}
const week = 7 * 86400000;
export interface ImportResult {
  id: string;
  alreadyApplied: boolean;
  ok: boolean;
  errors: string[];
  warnings: string[];
  counts: {
    players: number;
    newPlayers: number;
    existingPlayers: number;
    matches: number;
    changes: number;
  };
  mappings: ImportBundle['mappings'];
  latestAt: number | null;
  target?: string;
}
export class RecordStore {
  constructor(
    private ctx: DurableObjectState,
    private env: Env,
    private read: (guild: string) => Snapshot,
  ) {}
  private get sql() {
    return this.ctx.storage.sql;
  }
  private bump() {
    this.sql.exec('UPDATE record_state SET epoch=epoch+1 WHERE id=1');
  }
  private state(guild: string) {
    const s = this.read(guild);
    return `${s.revision}:${this.sql.exec<{ epoch: number }>('SELECT epoch FROM record_state WHERE id=1').toArray()[0].epoch}`;
  }
  private player(game: string, user: string): Player | null {
    const row = this.sql
      .exec<{ config: string }>(
        'SELECT config FROM profiles WHERE game_id=? AND user_id=?',
        game,
        user,
      )
      .toArray()[0];
    return row ? JSON.parse(row.config) : null;
  }
  private audit(
    game: string,
    user: string | null,
    kind: string,
    actor: string,
    details: unknown,
    id = crypto.randomUUID(),
  ) {
    this.sql.exec(
      'INSERT INTO record_audit VALUES(?,?,?,?,?,?,?)',
      id,
      game,
      user,
      kind,
      actor,
      Date.now(),
      JSON.stringify(details),
    );
  }
  changed(game: string, user: string) {
    this.sql.exec('INSERT OR IGNORE INTO player_meta(game_id,user_id) VALUES(?,?)', game, user);
    this.bump();
    // Jobs are coalesced by player. A failed Discord delivery never rolls back a rating.
    const identity = this.sql
      .exec<{ guild_id: string }>('SELECT guild_id FROM community WHERE id=1')
      .toArray()[0];
    if (!identity) return;
    const p = recordPolicy(this.read(identity.guild_id).settings, game);
    if (p.decorateNickname || p.ranks.some((r) => r.roleId))
      this.sql.exec(
        "INSERT INTO rank_jobs VALUES(?,?,1,?,'pending',0,NULL) ON CONFLICT(game_id,user_id) DO UPDATE SET version=version+1,due=excluded.due,status='pending',attempts=0,error=NULL",
        game,
        user,
        Date.now(),
      );
  }
  index(m: Match) {
    const affected = new Set([
      ...this.sql
        .exec<{ user_id: string }>('SELECT user_id FROM participants WHERE match_id=?', m.id)
        .toArray()
        .map((p) => p.user_id),
      ...m.roster.map((p) => p.id),
    ]);
    // A substitute replaces the participant; the outgoing player must not
    // acquire a finished match or last-played date from the replacement's game.
    this.sql.exec('DELETE FROM participants WHERE match_id=?', m.id);
    for (const p of m.roster)
      this.sql.exec(
        'INSERT INTO participants VALUES(?,?,?,?,?) ON CONFLICT(match_id,user_id) DO UPDATE SET at=excluded.at,state=excluded.state',
        m.queue.gameId,
        p.id,
        m.id,
        m.closedAt ?? m.createdAt,
        m.state,
      );
    for (const user of affected) {
      const at = this.sql
        .exec<{ at: number | null }>(
          "SELECT MAX(at) AS at FROM (SELECT at FROM participants WHERE game_id=? AND user_id=? AND state='finished' UNION ALL SELECT last_ranked AS at FROM player_baselines WHERE game_id=? AND user_id=?)",
          m.queue.gameId,
          user,
          m.queue.gameId,
          user,
        )
        .toArray()[0].at;
      this.sql.exec(
        'INSERT INTO player_meta(game_id,user_id,last_ranked) VALUES(?,?,?) ON CONFLICT(game_id,user_id) DO UPDATE SET last_ranked=excluded.last_ranked',
        m.queue.gameId,
        user,
        at,
      );
    }
    this.bump();
  }
  backfill() {
    if (this.sql.exec('SELECT 1 FROM record_state WHERE epoch>0').toArray().length) return;
    for (const r of this.sql.exec<{ config: string }>('SELECT config FROM matches').toArray())
      this.index(JSON.parse(r.config));
    this.sql.exec(
      'INSERT OR IGNORE INTO player_meta(game_id,user_id) SELECT game_id,user_id FROM profiles',
    );
    this.bump();
  }
  clocks(guild: string) {
    const s = this.read(guild).settings;
    for (const row of this.sql
      .exec<{ game_id: string }>('SELECT game_id FROM record_clocks')
      .toArray())
      if (
        !s.games.some(
          (g) =>
            g.id === row.game_id &&
            recordPolicy(s, g.id).inactivity.enabled &&
            s.queues.some((q) => q.gameId === g.id),
        )
      )
        this.sql.exec('DELETE FROM record_clocks WHERE game_id=?', row.game_id);
    for (const g of s.games)
      if (recordPolicy(s, g.id).inactivity.enabled && s.queues.some((q) => q.gameId === g.id)) {
        // UTC Monday boundary; one durable weekly alarm, with missed weeks applied once.
        const monday = Math.floor((Date.now() - 4 * 86400000) / week) * week + 4 * 86400000 + week;
        this.sql.exec('INSERT OR IGNORE INTO record_clocks VALUES(?,?)', g.id, monday);
      }
  }
  configure(guild: string, old: Snapshot['settings']) {
    const s = this.read(guild).settings;
    for (const g of s.games) {
      const previous = recordPolicy(old, g.id),
        next = recordPolicy(s, g.id);
      if (
        canonical({ ranks: previous.ranks, decorateNickname: previous.decorateNickname }) ===
        canonical({ ranks: next.ranks, decorateNickname: next.decorateNickname })
      )
        continue;
      for (const row of this.sql
        .exec<{ user_id: string }>('SELECT user_id FROM profiles WHERE game_id=?', g.id)
        .toArray()) {
        for (const role of previous.ranks.map((r) => r.roleId).filter((id): id is string => !!id))
          if (!next.ranks.some((r) => r.roleId === role))
            this.sql.exec(
              'INSERT OR IGNORE INTO retired_ranks VALUES(?,?,?)',
              g.id,
              row.user_id,
              role,
            );
        if (previous.decorateNickname && !next.decorateNickname)
          this.sql.exec(
            'INSERT OR IGNORE INTO retired_ranks VALUES(?,?,?)',
            g.id,
            row.user_id,
            'nickname',
          );
        if (
          previous.decorateNickname ||
          next.decorateNickname ||
          previous.ranks.some((r) => r.roleId) ||
          next.ranks.some((r) => r.roleId)
        )
          this.sql.exec(
            "INSERT INTO rank_jobs VALUES(?,?,1,?,'pending',0,NULL) ON CONFLICT(game_id,user_id) DO UPDATE SET version=version+1,due=excluded.due,status='pending',attempts=0,error=NULL",
            g.id,
            row.user_id,
            Date.now(),
          );
      }
    }
    this.clocks(guild);
  }
  decay(guild: string, now = Date.now()) {
    const s = this.read(guild).settings;
    for (const clock of this.sql
      .exec<{ game_id: string; due: number }>('SELECT * FROM record_clocks WHERE due<=?', now)
      .toArray()) {
      const cfg = recordPolicy(s, clock.game_id),
        q = s.queues.find((q) => q.gameId === clock.game_id);
      if (cfg.inactivity.enabled && q) {
        const rows = this.sql
          .exec<{ config: string; user_id: string }>(
            'SELECT p.config,p.user_id FROM profiles p JOIN player_meta m USING(game_id,user_id) WHERE p.game_id=? AND m.last_ranked<? AND NOT EXISTS(SELECT 1 FROM active_players a WHERE a.user_id=p.user_id)',
            clock.game_id,
            now - 30 * 86400000,
          )
          .toArray();
        for (const row of rows) {
          const p: Player = JSON.parse(row.config),
            before = p.rating,
            rd = p.deviation,
            floor = rankLabel(s, clock.game_id, p.rating)?.threshold ?? 0;
          if (floor > 0) p.rating = Math.max(floor, p.rating - cfg.inactivity.weeklyRating);
          p.deviation = Math.min(policy(q).deviation, p.deviation + cfg.inactivity.weeklyDeviation);
          if (p.rating === before && p.deviation === rd) continue;
          this.sql.exec(
            'UPDATE profiles SET config=? WHERE game_id=? AND user_id=?',
            JSON.stringify(p),
            clock.game_id,
            p.id,
          );
          const id = `decay:${clock.game_id}:${clock.due}:${p.id}`;
          this.sql.exec(
            'INSERT INTO rating_changes VALUES(?,?,?,?,?,?,?,?)',
            id,
            clock.game_id,
            p.id,
            null,
            before,
            p.rating,
            now,
            'system',
          );
          this.sql.exec(
            'INSERT INTO rating_details VALUES(?,?,?,?,?)',
            id,
            rd,
            p.deviation,
            'Weekly inactivity decay',
            'local',
          );
          this.changed(clock.game_id, p.id);
        }
        this.audit(clock.game_id, null, 'decay', 'system', {
          due: clock.due,
          eligible: rows.length,
        });
      }
      this.sql.exec(
        'UPDATE record_clocks SET due=? WHERE game_id=?',
        clock.due + (Math.floor((now - clock.due) / week) + 1) * week,
        clock.game_id,
      );
    }
  }
  statistics(
    guild: string,
    game: string,
    user?: string,
    page = 0,
    admin = false,
    viewer?: string,
  ): Statistics {
    const s = this.read(guild).settings;
    if (!s.games.some((g) => g.id === game))
      throw new PlayError('Game not found in this community.');
    const cfg = recordPolicy(s, game),
      cutoff = cfg.recentDays ? Date.now() - cfg.recentDays * 86400000 : 0;
    const eligible = `p.game_id=? AND COALESCE(m.hidden,0)=0 AND json_extract(p.config,'$.wins')+json_extract(p.config,'$.losses')+json_extract(p.config,'$.draws')>=? AND (?=0 OR m.last_ranked>?)`;
    const total = this.sql
      .exec<{ n: number }>(
        `SELECT COUNT(*) AS n FROM profiles p LEFT JOIN player_meta m USING(game_id,user_id) WHERE ${eligible}`,
        game,
        cfg.minimumMatches,
        cutoff,
        cutoff,
      )
      .toArray()[0].n;
    const rows = this.sql
      .exec<{ config: string; last_ranked: number | null }>(
        `SELECT p.config,m.last_ranked FROM profiles p LEFT JOIN player_meta m USING(game_id,user_id) WHERE ${eligible} ORDER BY json_extract(p.config,'$.rating') DESC,p.user_id LIMIT 10 OFFSET ?`,
        game,
        cfg.minimumMatches,
        cutoff,
        cutoff,
        page * 10,
      )
      .toArray();
    const leaderboard = rows.map((row) => {
      const p: Player = JSON.parse(row.config);
      return {
        ...p,
        lastRankedAt: row.last_ranked,
        rank: rankLabel(s, game, p.rating)?.label ?? null,
      };
    });
    let detail: PlayerStatistics | null = null;
    if (user) {
      const p = this.player(game, user),
        meta = this.sql
          .exec<{ hidden: number; last_ranked: number | null; source: string }>(
            'SELECT * FROM player_meta WHERE game_id=? AND user_id=?',
            game,
            user,
          )
          .toArray()[0];
      if (p && (!meta?.hidden || admin || viewer === user)) {
        const history = this.sql
          .exec<RatingHistory & Record<string, SqlStorageValue>>(
            'SELECT c.*,d.deviation_before,d.deviation_after,d.reason,d.source FROM rating_changes c LEFT JOIN rating_details d ON c.id=d.change_id WHERE c.game_id=? AND c.user_id=? ORDER BY changed_at DESC,c.id DESC LIMIT 30',
            game,
            user,
          )
          .toArray();
        const peak = this.sql
          .exec<{ peak: number | null }>(
            "SELECT MAX(MAX(c.before,c.after)) AS peak FROM rating_changes c LEFT JOIN matches m ON c.match_id=m.id WHERE c.game_id=? AND c.user_id=? AND (c.match_id IS NULL OR m.state='finished') AND c.id NOT LIKE 'undo:%'",
            game,
            user,
          )
          .toArray()[0].peak;
        const hiddenIds = new Set(
          this.sql
            .exec<{ user_id: string }>(
              'SELECT user_id FROM player_meta WHERE game_id=? AND hidden=1',
              game,
            )
            .toArray()
            .map((r) => r.user_id),
        );
        const recent = this.sql
          .exec<{ config: string }>(
            'SELECT m.config FROM matches m JOIN participants p ON m.id=p.match_id WHERE p.game_id=? AND p.user_id=? ORDER BY p.at DESC,m.id DESC LIMIT 30',
            game,
            user,
          )
          .toArray()
          .map((row) => {
            const m: Match = JSON.parse(row.config);
            return {
              id: m.id,
              at: m.closedAt ?? m.createdAt,
              state: m.state,
              result: m.result,
              queue: m.queue.name,
              note: m.note ?? null,
              teams: m.teams.map((t) =>
                t.map((p) =>
                  !admin && hiddenIds.has(p.id) && p.id !== viewer
                    ? { id: null, name: 'Hidden player' }
                    : { id: p.id, name: p.name },
                ),
              ),
            };
          });
        const matches = p.wins + p.losses + p.draws;
        const relations = new Map<string, PlayerStatistics['relationships'][number]>();
        for (const m of recent.filter((m) => m.state === 'finished')) {
          const side = m.teams.findIndex((t) => t.some((p) => p.id === user));
          if (side < 0) continue;
          const won = m.result === (side === 0 ? 'alpha' : 'beta');
          for (const [i, team] of m.teams.entries())
            for (const other of team) {
              if (!other.id || other.id === user) continue;
              const r = relations.get(other.id) ?? {
                userId: other.id,
                name: other.name,
                teammateMatches: 0,
                opponentMatches: 0,
                winsTogether: 0,
                winsAgainst: 0,
              };
              if (i === side) {
                r.teammateMatches++;
                if (won) r.winsTogether++;
              } else {
                r.opponentMatches++;
                if (won) r.winsAgainst++;
              }
              relations.set(other.id, r);
            }
        }
        detail = {
          ...p,
          hidden: !!meta?.hidden,
          lastRankedAt: meta?.last_ranked ?? null,
          source: meta?.source ?? 'local',
          rank: rankLabel(s, game, p.rating)?.label ?? null,
          peak: Math.max(p.rating, peak ?? p.rating),
          matchCount: matches,
          winRate: matches ? p.wins / matches : null,
          history,
          recent,
          relationships: [...relations.values()].filter(
            (r) => r.teammateMatches + r.opponentMatches >= 3,
          ),
          profiles: this.sql
            .exec<ProfileReference & Record<string, SqlStorageValue>>(
              'SELECT provider,external_id,url,provenance FROM profile_refs WHERE game_id=? AND user_id=?',
              game,
              user,
            )
            .toArray(),
          eligible:
            !meta?.hidden &&
            matches >= cfg.minimumMatches &&
            (!cutoff || (meta?.last_ranked ?? 0) > cutoff),
        };
      }
    }
    return {
      gameId: game,
      gameName: s.games.find((g) => g.id === game)!.name,
      visibility: cfg.visibility,
      policy: { minimumMatches: cfg.minimumMatches, recentDays: cfg.recentDays },
      page,
      total,
      pages: Math.ceil(total / 10),
      leaderboard,
      player: detail,
    };
  }
  action(
    guild: string,
    input: RecordAction,
    actor: string,
    admin: boolean,
  ): { ok: true; content: string } {
    const a = recordActionSchema.parse(input),
      s = this.read(guild).settings;
    if (!s.games.some((g) => g.id === a.gameId)) throw new PlayError('Game not found.');
    const fingerprint = canonical({ a, actor, admin });
    const old = this.sql
      .exec<{ fingerprint: string; result: string }>(
        'SELECT * FROM record_actions WHERE id=?',
        a.id,
      )
      .toArray()[0];
    if (old) {
      if (old.fingerprint !== fingerprint)
        throw new PlayError('That action ID belongs to another request.');
      return JSON.parse(old.result);
    }
    if (!admin && (a.userId !== actor || !['link', 'unlink'].includes(a.kind)))
      throw new PlayError(
        'Only organizers can change visibility, reassign profiles or synchronize ranks.',
      );
    if (!this.player(a.gameId, a.userId))
      throw new PlayError('Play a match or seed this player before managing their records.');
    let content = '',
      previousOwner: string | undefined;
    this.ctx.storage.transactionSync(() => {
      if (a.kind === 'hide' || a.kind === 'unhide') {
        this.sql.exec(
          'INSERT INTO player_meta(game_id,user_id,hidden) VALUES(?,?,?) ON CONFLICT(game_id,user_id) DO UPDATE SET hidden=excluded.hidden',
          a.gameId,
          a.userId,
          a.kind === 'hide' ? 1 : 0,
        );
        content =
          a.kind === 'hide'
            ? 'Player hidden from leaderboards and other players’ statistics.'
            : 'Player is visible when leaderboard thresholds are met.';
      } else if (a.kind === 'sync') {
        this.changed(a.gameId, a.userId);
        content = 'Rank synchronization queued if roles or nickname decoration are configured.';
      } else {
        if (!a.provider || !a.externalId)
          throw new PlayError('Choose a provider and external profile ID.');
        const existing = this.sql
          .exec<{ user_id: string }>(
            'SELECT user_id FROM profile_refs WHERE game_id=? AND provider=? AND external_id=?',
            a.gameId,
            a.provider,
            a.externalId,
          )
          .toArray()[0];
        previousOwner = existing?.user_id;
        if (a.kind === 'unlink') {
          if (existing?.user_id !== a.userId)
            throw new PlayError('That profile is not linked to this player.');
          this.sql.exec(
            'DELETE FROM profile_refs WHERE game_id=? AND provider=? AND external_id=?',
            a.gameId,
            a.provider,
            a.externalId,
          );
          content = 'Profile reference removed.';
        } else {
          if (!a.url) throw new PlayError('Provide an HTTPS profile URL.');
          if (existing && existing.user_id !== a.userId && a.kind !== 'reassign')
            throw new PlayError(
              'That external profile is already linked to another player. An organizer must explicitly reassign it.',
            );
          if (a.kind === 'reassign' && (!admin || !a.reason))
            throw new PlayError('An organizer must provide a reason for reassignment.');
          this.sql.exec(
            'INSERT INTO profile_refs VALUES(?,?,?,?,?,?,?) ON CONFLICT(game_id,provider,external_id) DO UPDATE SET user_id=excluded.user_id,url=excluded.url,provenance=excluded.provenance,at=excluded.at',
            a.gameId,
            a.provider,
            a.externalId,
            a.userId,
            a.url,
            admin ? 'admin' : 'player',
            Date.now(),
          );
          content = 'Profile reference saved. It has not been verified against a game API.';
        }
      }
      this.bump();
      this.audit(a.gameId, a.userId, a.kind, actor, { ...a, previousOwner });
      this.sql.exec(
        'INSERT INTO record_actions VALUES(?,?,?,?)',
        a.id,
        fingerprint,
        JSON.stringify({ ok: true, content }),
        Date.now(),
      );
      this.sql.exec('DELETE FROM record_actions WHERE at<?', Date.now() - 7 * 86400000);
    });
    return { ok: true, content };
  }
  private inspect(guild: string, b: ImportBundle) {
    const s = this.read(guild).settings,
      errors: string[] = [],
      warnings: string[] = [],
      mapped = new Map(b.mappings.map((m) => [m.ladder, m]));
    if (mapped.size !== b.mappings.length) errors.push('Duplicate source ladder mapping.');
    if (new Set(b.mappings.map((m) => m.gameId)).size !== b.mappings.length)
      errors.push(
        'Independent source ladders cannot be merged into one destination game. Map shared queues to one source ladder.',
      );
    for (const m of b.mappings) {
      if (!s.games.some((g) => g.id === m.gameId))
        errors.push(`Unknown destination game for ${m.ladder}.`);
      for (const q of Object.values(m.queues))
        if (!s.queues.some((queue) => queue.id === q && queue.gameId === m.gameId))
          errors.push(`Queue mapping does not belong to ${m.ladder}'s game.`);
      if (
        b.mode === 'history' &&
        (!m.engine || s.queues.some((q) => q.gameId === m.gameId && q.ratingEngine !== m.engine))
      )
        errors.push('Full history requires an explicit matching source rating model.');
    }
    const ids = new Set<string>(),
      players = new Map<string, Player>();
    let insert = 0,
      skip = 0;
    for (const row of b.players) {
      const key = `${row.ladder}:${row.player.id}`;
      if (players.has(key)) errors.push(`Duplicate player ${key}.`);
      players.set(key, row.player);
      if (row.player.streak > row.player.wins || -row.player.streak > row.player.losses)
        errors.push(`Streak exceeds the win/loss totals for ${key}.`);
      const map = mapped.get(row.ladder);
      if (!map) {
        errors.push('Unmapped player ladder.');
        continue;
      }
      if (this.player(map.gameId, row.player.id)) skip++;
      else insert++;
      if (row.lastRankedAt && row.lastRankedAt > Date.now() + 86400000)
        errors.push('Player last-played date is in the future.');
    }
    const counts = new Map<
      string,
      { wins: number; losses: number; draws: number; last: number | null }
    >();
    const sourceMatches = new Map(b.matches.map((m) => [`${m.ladder}:${m.sourceId}`, m]));
    const outcomes = new Map<string, { at: number; id: string; sign: number }[]>();
    for (const m of b.matches) {
      const key = `${m.ladder}:${m.sourceId}`;
      if (ids.has(key)) errors.push(`Duplicate match source ID ${key}.`);
      ids.add(key);
      const mapping = mapped.get(m.ladder);
      if (!mapping?.queues[m.queue]) errors.push(`Unmapped source queue ${m.queue}.`);
      const roster = m.teams.flat();
      if (new Set(roster).size !== roster.length)
        errors.push(`Duplicate participant in match ${m.sourceId}.`);
      if (m.at > m.endedAt || m.endedAt > Date.now() + 86400000)
        errors.push('Invalid match timestamps.');
      for (const [side, team] of m.teams.entries())
        for (const user of team) {
          const pk = `${m.ladder}:${user}`;
          if (!players.has(pk)) errors.push(`Missing player for match ${m.sourceId}.`);
          const c = counts.get(pk) ?? { wins: 0, losses: 0, draws: 0, last: null };
          if (m.result !== 'abort') {
            const list = outcomes.get(pk) ?? [];
            list.push({
              at: m.endedAt,
              id: m.sourceId,
              sign: m.result === 'draw' ? 0 : m.result === (side === 0 ? 'alpha' : 'beta') ? 1 : -1,
            });
            outcomes.set(pk, list);
            c.last = Math.max(c.last ?? 0, m.endedAt);
            c[
              m.result === 'draw'
                ? 'draws'
                : m.result === (side === 0 ? 'alpha' : 'beta')
                  ? 'wins'
                  : 'losses'
            ]++;
          }
          counts.set(pk, c);
        }
    }
    const changes = new Set<string>();
    const chains = new Map<string, typeof b.changes>();
    const matchChanges = new Map<string, number>();
    for (const c of b.changes) {
      const key = `${c.ladder}:${c.sourceId}`;
      if (changes.has(key)) errors.push(`Duplicate rating-history source ID ${key}.`);
      changes.add(key);
      if (!players.has(`${c.ladder}:${c.userId}`))
        errors.push('Rating history refers to an unknown player.');
      if (c.matchId && !ids.has(`${c.ladder}:${c.matchId}`))
        errors.push('Rating history refers to an unknown match.');
      if (c.at > Date.now() + 86400000) errors.push('Rating history timestamp is in the future.');
      if (c.matchId) {
        const m = sourceMatches.get(`${c.ladder}:${c.matchId}`);
        const key = `${c.ladder}:${c.matchId}:${c.userId}`;
        matchChanges.set(key, (matchChanges.get(key) ?? 0) + 1);
        if (m && (!m.teams.flat().includes(c.userId) || m.result === 'abort' || c.at < m.at))
          errors.push(
            'Rating history is inconsistent with its match participant, result or timestamp.',
          );
      }
      const pk = `${c.ladder}:${c.userId}`;
      const chain = chains.get(pk) ?? [];
      chain.push(c);
      chains.set(pk, chain);
    }
    if (b.mode === 'seed' && (b.matches.length || b.changes.length))
      errors.push('A seed import must not contain matches or history.');
    if (b.mode === 'history') {
      for (const m of b.matches)
        if (m.result !== 'abort')
          for (const user of m.teams.flat())
            if (matchChanges.get(`${m.ladder}:${m.sourceId}:${user}`) !== 1)
              errors.push(
                'Every ranked participant requires exactly one original rating/deviation history row.',
              );
      for (const m of b.matches)
        if (
          this.sql
            .exec(
              'SELECT 1 FROM import_sources WHERE source=? AND kind=? AND source_id=?',
              `${b.source}:${b.sourceId}`,
              'match',
              `${m.ladder}:${m.sourceId}`,
            )
            .toArray().length
        )
          errors.push(
            'These source matches were already imported. Use the original manifest or review the source mapping.',
          );
      for (const mapping of b.mappings)
        if (
          this.sql.exec('SELECT 1 FROM profiles WHERE game_id=? LIMIT 1', mapping.gameId).toArray()
            .length ||
          this.sql.exec('SELECT 1 FROM matches WHERE game_id=? LIMIT 1', mapping.gameId).toArray()
            .length ||
          this.sql
            .exec(
              'SELECT 1 FROM queue_entries WHERE queue_id IN(SELECT id FROM queues WHERE game_id=?) LIMIT 1',
              mapping.gameId,
            )
            .toArray().length
        )
          errors.push(
            'Full-history imports require an empty destination game. Use a new game for rehearsal.',
          );
      for (const [key, p] of players) {
        const c = counts.get(key) ?? { wins: 0, losses: 0, draws: 0, last: null };
        if (p.wins !== c.wins || p.losses !== c.losses || p.draws !== c.draws)
          errors.push(`Match totals do not reconcile for ${key}.`);
        const results = (outcomes.get(key) ?? []).sort(
          (a, b) => a.at - b.at || a.id.localeCompare(b.id),
        );
        let streak = 0;
        for (const result of results)
          streak = !result.sign
            ? 0
            : Math.sign(streak) === result.sign
              ? streak + result.sign
              : result.sign;
        if (
          results.some(
            (r, i) => i > 0 && r.at === results[i - 1].at && r.sign !== results[i - 1].sign,
          )
        )
          errors.push(
            `Ambiguous simultaneous match results for ${key}; supply distinct original report timestamps.`,
          );
        else if (p.streak !== streak) errors.push(`Win/loss streak does not reconcile for ${key}.`);
        const row = b.players.find((r) => `${r.ladder}:${r.player.id}` === key)!;
        if (row.lastRankedAt !== undefined && row.lastRankedAt !== c.last)
          errors.push(`Last-played timestamp does not reconcile for ${key}.`);
        const history = (chains.get(key) ?? []).sort(
          (a, b) => a.at - b.at || a.sourceId.localeCompare(b.sourceId),
        );
        for (let i = 1; i < history.length; i++)
          if (
            history[i].before !== history[i - 1].after ||
            history[i].deviationBefore !== history[i - 1].deviationAfter
          )
            errors.push(`Rating history has a gap for ${key}.`);
        if (
          history.length &&
          (history.at(-1)!.after !== p.rating || history.at(-1)!.deviationAfter !== p.deviation)
        )
          errors.push(`Final rating/deviation do not reconcile for ${key}.`);
      }
    }
    if (skip) warnings.push(`${skip} existing player records will be retained without changes.`);
    if (b.unsupported.length) warnings.push(`Not imported: ${b.unsupported.join(', ')}.`);
    warnings.push(
      'Ratings imports do not include game-profile identities, gold, quizzes or drafting data.',
    );
    return {
      ok: errors.length === 0,
      errors: [...new Set(errors)].slice(0, 100),
      warnings,
      counts: {
        players: b.players.length,
        newPlayers: insert,
        existingPlayers: skip,
        matches: b.matches.length,
        changes: b.changes.length,
      },
      mappings: b.mappings,
      latestAt: b.matches.length ? Math.max(...b.matches.map((m) => m.endedAt)) : null,
    };
  }
  async preview(guild: string, input: ImportBundle): Promise<ImportResult> {
    const b = importSchema.parse(input),
      id = await digest({ guild, b });
    const old = this.sql
      .exec<{ summary: string }>('SELECT summary FROM imports WHERE id=?', id)
      .toArray()[0];
    if (old)
      return { id, target: this.state(guild), alreadyApplied: true, ...JSON.parse(old.summary) };
    return { id, target: this.state(guild), alreadyApplied: false, ...this.inspect(guild, b) };
  }
  async apply(
    guild: string,
    input: ImportBundle,
    target: string,
    actor: string,
  ): Promise<ImportResult> {
    const b = importSchema.parse(input),
      id = await digest({ guild, b });
    this.read(guild);
    return this.ctx.storage.transactionSync(() => {
      const old = this.sql
        .exec<{ summary: string }>('SELECT summary FROM imports WHERE id=?', id)
        .toArray()[0];
      if (old) return { id, alreadyApplied: true, ...JSON.parse(old.summary) };
      if (target !== this.state(guild))
        throw new PlayError(
          'Records or settings changed after preview. Preview this import again.',
        );
      const summary = this.inspect(guild, b);
      if (!summary.ok) throw new PlayError(summary.errors.join(' ').slice(0, 1000));
      const s = this.read(guild).settings,
        mapping = new Map(b.mappings.map((m) => [m.ladder, m])),
        source = `${b.source}:${b.sourceId}`;
      for (const row of b.players) {
        const game = mapping.get(row.ladder)!.gameId,
          p = row.player;
        if (this.player(game, p.id)) continue;
        const q = s.queues.find((q) => q.gameId === game);
        if (!q) throw new PlayError('Add a destination queue before importing.');
        this.sql.exec('INSERT INTO profiles VALUES(?,?,?)', game, p.id, JSON.stringify(p));
        this.sql.exec('INSERT OR IGNORE INTO rating_engines VALUES(?,?)', game, q.ratingEngine);
        this.sql.exec(
          'INSERT INTO player_meta VALUES(?,?,?,?,?)',
          game,
          p.id,
          row.hidden ? 1 : 0,
          row.lastRankedAt ?? null,
          source,
        );
        this.changed(game, p.id);
        if (b.mode === 'seed')
          this.sql.exec(
            'INSERT INTO player_baselines VALUES(?,?,?,?)',
            game,
            p.id,
            JSON.stringify(p),
            row.lastRankedAt ?? null,
          );
      }
      for (const row of b.matches) {
        const map = mapping.get(row.ladder)!,
          q = s.queues.find((q) => q.id === map.queues[row.queue])!,
          mid = `${id.slice(0, 8)}-${id.slice(8, 12)}-4${id.slice(13, 16)}-a${id.slice(17, 20)}-${(id.slice(20, 28) + String(b.matches.indexOf(row)).padStart(4, '0')).slice(0, 12)}`;
        const hist = b.changes.filter((c) => c.ladder === row.ladder && c.matchId === row.sourceId);
        const roster = row.teams.flat().map((user) => {
            const p = this.player(map.gameId, user)!,
              c = hist.find((c) => c.userId === user);
            return c ? { ...p, rating: c.before, deviation: c.deviationBefore } : p;
          }),
          m: Match = {
            id: mid,
            queue: q,
            gameName: s.games.find((g) => g.id === map.gameId)!.name,
            roster,
            teams: row.teams.map((team) =>
              team.map((user) => roster.find((p) => p.id === user)!),
            ) as [Player[], Player[]],
            captains: [row.teams[0][0], row.teams[1][0]],
            ready: [],
            state: row.result === 'abort' ? 'aborted' : 'finished',
            result: row.result === 'abort' ? null : row.result,
            agreements: {},
            createdAt: row.at,
            closedAt: row.endedAt,
            deadline: null,
            version: 1,
            changes: hist.map((c) => ({ userId: c.userId, before: c.before, after: c.after })),
            ratedPlayers: hist.map((c) => ({
              ...this.player(map.gameId, c.userId)!,
              rating: c.after,
              deviation: c.deviationAfter,
            })),
            note: `Imported ${source}; source match ${row.sourceId}.`,
          };
        this.sql.exec(
          'INSERT INTO matches VALUES(?,?,?,?,?,?,?)',
          mid,
          map.gameId,
          q.id,
          q.channelId,
          m.state,
          m.createdAt,
          JSON.stringify(m),
        );
        this.sql.exec(
          'INSERT INTO import_sources VALUES(?,?,?,?)',
          source,
          'match',
          `${row.ladder}:${row.sourceId}`,
          mid,
        );
        this.index(m);
      }
      for (const c of b.changes) {
        const game = mapping.get(c.ladder)!.gameId,
          change = `import:${id}:${c.ladder}:${c.sourceId}`,
          mid = c.matchId
            ? this.sql
                .exec<{ local_id: string }>(
                  "SELECT local_id FROM import_sources WHERE source=? AND kind='match' AND source_id=?",
                  source,
                  `${c.ladder}:${c.matchId}`,
                )
                .toArray()[0].local_id
            : null;
        this.sql.exec(
          'INSERT INTO rating_changes VALUES(?,?,?,?,?,?,?,?)',
          change,
          game,
          c.userId,
          mid,
          c.before,
          c.after,
          c.at,
          actor,
        );
        this.sql.exec(
          'INSERT INTO rating_details VALUES(?,?,?,?,?)',
          change,
          c.deviationBefore,
          c.deviationAfter,
          c.reason,
          source,
        );
        this.sql.exec(
          'INSERT INTO import_sources VALUES(?,?,?,?)',
          source,
          'change',
          `${c.ladder}:${c.sourceId}`,
          change,
        );
      }
      this.sql.exec(
        'INSERT INTO imports VALUES(?,?,?,?,?)',
        id,
        source,
        Date.now(),
        actor,
        JSON.stringify(summary),
      );
      for (const m of b.mappings)
        this.audit(m.gameId, null, 'import', actor, { id, source, counts: summary.counts });
      this.bump();
      return { id, alreadyApplied: false, ...summary };
    });
  }
  diagnostics(guild: string) {
    this.read(guild);
    return {
      imports: this.sql.exec('SELECT * FROM imports ORDER BY at DESC LIMIT 20').toArray(),
      audit: this.sql.exec('SELECT * FROM record_audit ORDER BY at DESC LIMIT 30').toArray(),
      ranks: this.sql.exec('SELECT * FROM rank_jobs ORDER BY due LIMIT 50').toArray(),
      clocks: this.sql.exec('SELECT * FROM record_clocks').toArray(),
      profiles: this.sql.exec('SELECT * FROM player_meta LIMIT 100').toArray(),
    };
  }
  async ranks(guild: string) {
    let roles: { id: string; managed?: boolean; permissions: string }[] | undefined;
    let channels:
      { permission_overwrites?: { id: string; type: number; allow: string }[] }[] | undefined;
    for (let i = 0; i < 8; i++) {
      const job = this.sql
        .exec<{ game_id: string; user_id: string; version: number; attempts: number }>(
          "SELECT * FROM rank_jobs WHERE status='pending' AND due<=? ORDER BY due LIMIT 1",
          Date.now(),
        )
        .toArray()[0];
      if (!job) break;
      const s = this.read(guild).settings,
        cfg = recordPolicy(s, job.game_id),
        p = this.player(job.game_id, job.user_id);
      if (!p) break;
      const gate = this.env.DISCORD_GATE.getByName(this.env.DISCORD_APPLICATION_ID!),
        route = `guild:${guild}:member:${job.user_id}`,
        due = await gate.reserve(route);
      if (due > Date.now()) {
        this.sql.exec(
          'UPDATE rank_jobs SET due=? WHERE game_id=? AND user_id=? AND version=?',
          due,
          job.game_id,
          job.user_id,
          job.version,
        );
        continue;
      }
      const headers = {
        Authorization: `Bot ${this.env.DISCORD_BOT_TOKEN}`,
        'Content-Type': 'application/json',
      };
      const request = async (path: string, init: RequestInit = {}) => {
        const at = await gate.reserve(route);
        if (at > Date.now())
          throw Response.json({ retry_after: (at - Date.now()) / 1000 }, { status: 429 });
        const r = await fetch(`https://discord.com/api/v10${path}`, {
          ...init,
          headers,
          signal: AbortSignal.timeout(5000),
        });
        if (!r.ok) throw r;
        const remaining = r.headers.get('X-RateLimit-Remaining'),
          reset = Number(r.headers.get('X-RateLimit-Reset-After'));
        if (remaining === '0' && Number.isFinite(reset))
          await gate.pause(route, Date.now() + Math.ceil(reset * 1000));
        return r;
      };
      try {
        const member = (await (
          await request(`/guilds/${guild}/members/${job.user_id}`)
        ).json()) as {
          roles: string[];
          nick?: string | null;
          user: { username: string; global_name?: string | null };
        };
        const desired = rankLabel(s, job.game_id, p.rating)?.roleId ?? null;
        const retired = this.sql
          .exec<{ role_id: string }>(
            'SELECT role_id FROM retired_ranks WHERE game_id=? AND user_id=?',
            job.game_id,
            job.user_id,
          )
          .toArray()
          .map((r) => r.role_id);
        const managedRoles = new Set([
          ...cfg.ranks.map((r) => r.roleId).filter((id): id is string => !!id),
          ...retired.filter((id) => id !== 'nickname'),
        ]);
        if (managedRoles.size) {
          roles ??= (await (await request(`/guilds/${guild}/roles`)).json()) as typeof roles;
          for (const id of managedRoles) {
            const role = roles?.find((r) => r.id === id);
            if (
              !role ||
              id === guild ||
              role.managed ||
              BigInt(role.permissions) & privilegedRankPermissions
            )
              throw new PlayError(
                'Rank role is missing, managed or privileged. Configure a cosmetic role and retry synchronization.',
              );
          }
          channels ??= (await (
            await request(`/guilds/${guild}/channels`)
          ).json()) as typeof channels;
          if (
            channels?.some((c) =>
              c.permission_overwrites?.some(
                (o) =>
                  o.type === 0 &&
                  managedRoles.has(o.id) &&
                  BigInt(o.allow) & privilegedRankPermissions,
              ),
            )
          )
            throw new PlayError(
              'Rank role has a privileged channel override. Configure a cosmetic role and retry synchronization.',
            );
        }
        for (const id of managedRoles)
          if (id !== desired && member.roles.includes(id)) {
            await request(`/guilds/${guild}/members/${job.user_id}/roles/${id}`, {
              method: 'DELETE',
            });
          }
        if (desired && !member.roles.includes(desired))
          await request(`/guilds/${guild}/members/${job.user_id}/roles/${desired}`, {
            method: 'PUT',
          });
        if (cfg.decorateNickname) {
          const base = (member.nick ?? member.user.global_name ?? member.user.username).replace(
              /^\[\d+\]\s*/,
              '',
            ),
            nick = `[${p.rating}] ${base}`.slice(0, 32);
          if (nick !== member.nick)
            await request(`/guilds/${guild}/members/${job.user_id}`, {
              method: 'PATCH',
              body: JSON.stringify({ nick }),
            });
        } else if (retired.includes('nickname') && member.nick && /^\[\d+\]\s/.test(member.nick))
          await request(`/guilds/${guild}/members/${job.user_id}`, {
            method: 'PATCH',
            body: JSON.stringify({ nick: member.nick.replace(/^\[\d+\]\s*/, '') }),
          });
        this.sql.exec(
          "UPDATE rank_jobs SET status='sent',error=NULL WHERE game_id=? AND user_id=? AND version=?",
          job.game_id,
          job.user_id,
          job.version,
        );
        if (
          this.sql
            .exec(
              'SELECT 1 FROM rank_jobs WHERE game_id=? AND user_id=? AND version=?',
              job.game_id,
              job.user_id,
              job.version,
            )
            .toArray().length
        )
          this.sql.exec(
            'DELETE FROM retired_ranks WHERE game_id=? AND user_id=?',
            job.game_id,
            job.user_id,
          );
      } catch (error) {
        let due = Date.now() + Math.min(900000, 1000 * 2 ** job.attempts),
          status = job.attempts >= 7 ? 'exhausted' : 'pending',
          message = 'Discord request failed.';
        if (error instanceof PlayError) {
          status = 'blocked';
          message = error.message;
        }
        if (error instanceof Response) {
          message = `Discord HTTP ${error.status}`;
          if ([400, 401, 403, 404].includes(error.status)) status = 'blocked';
          if (error.status === 429) {
            const body = (await error.json().catch(() => ({}))) as {
              retry_after?: number;
              global?: boolean;
            };
            due = Date.now() + Math.max(1000, Math.min(86400000, (body.retry_after ?? 1) * 1000));
            await gate.pause(route, due, !!body.global);
          }
        }
        this.sql.exec(
          'UPDATE rank_jobs SET status=?,due=?,attempts=attempts+1,error=? WHERE game_id=? AND user_id=? AND version=?',
          status,
          due,
          message,
          job.game_id,
          job.user_id,
          job.version,
        );
      }
    }
  }
}

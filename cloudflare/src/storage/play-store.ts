import { createGameRecordTables } from './game-records';
import { CivStore, createCivTables } from './civ-store';
import { LobbyStore } from './lobby-store';
import type { LobbyEvent } from '../domain/lobby';
import type { Env } from '../env';
import type { Queue, Snapshot, Settings } from '../domain/config';
import {
  actionSchema,
  balance,
  playable,
  PlayError,
  policy,
  type Action,
  type Match,
  type Reply,
} from '../domain/play';
import { rateTeams, type Player } from '../domain/rating';
import { matchCard, queueCard, queuePingCard, agreementCard, type Card } from '../discord/cards';
import { RecordStore } from './record-store';
import { createUxTables, UxStore } from './ux-store';
import { createEconomyTables, EconomyStore } from './economy-store';
import { beforeStories, afterStories } from '../domain/stories';
import { createMapRound, defaultMaps, resolveMap, voteMap } from '../domain/maps';
import type { VoiceMember } from '../domain/presence';
import {
  adminAllowed,
  confirmedAdminOps,
  type AdminRequest,
  type AdminView,
} from '../domain/admin';

const voiceInterval = 5 * 60 * 1000;
const queueVisibilityInterval = 15 * 60 * 1000;
type QueueVoiceCache = { due: number; observedAt: number; members: VoiceMember[] | null };

type Entry = { user_id: string; display_name: string };
function calculate(m: Match, result: 0 | 1 | null) {
  try {
    return rateTeams(m.teams, result, policy(m.queue), m.queue.ratingEngine);
  } catch (error) {
    throw new PlayError(
      error instanceof Error
        ? error.message
        : 'Rating calculation could not converge. Review the parameters.',
    );
  }
}
type MatchRow = { config: string };
type Work = {
  key: string;
  payload: string;
  version: number;
  due: number;
  attempts: number;
  status: string;
  error: string | null;
};
type Pointer = {
  key: string;
  channel_id: string;
  nonce: string;
  message_id: string | null;
  reconcile: number;
  announced: number;
  reconcile_cursor?: string | null;
  reconcile_pages?: number;
};
export function createPlayTables(sql: SqlStorage) {
  createGameRecordTables(sql);
  createCivTables(sql);
  createUxTables(sql);
  createEconomyTables(sql);
  sql.exec(
    'CREATE TABLE IF NOT EXISTS queue_entries (seq INTEGER PRIMARY KEY AUTOINCREMENT, queue_id TEXT NOT NULL, user_id TEXT NOT NULL, display_name TEXT NOT NULL, UNIQUE(queue_id,user_id))',
  );
  sql.exec('CREATE INDEX IF NOT EXISTS entry_user ON queue_entries(user_id)');
  sql.exec(
    'CREATE TABLE IF NOT EXISTS profiles (game_id TEXT NOT NULL, user_id TEXT NOT NULL, config TEXT NOT NULL, PRIMARY KEY(game_id,user_id))',
  );
  sql.exec(
    'CREATE TABLE IF NOT EXISTS rating_engines (game_id TEXT PRIMARY KEY, engine TEXT NOT NULL)',
  );
  sql.exec(
    'CREATE TABLE IF NOT EXISTS matches (id TEXT PRIMARY KEY, game_id TEXT NOT NULL, queue_id TEXT NOT NULL, channel_id TEXT NOT NULL, state TEXT NOT NULL, created_at INTEGER NOT NULL, config TEXT NOT NULL)',
  );
  sql.exec('CREATE INDEX IF NOT EXISTS match_channel ON matches(channel_id,created_at DESC)');
  sql.exec('CREATE INDEX IF NOT EXISTS match_game ON matches(game_id,created_at DESC)');
  sql.exec(
    'CREATE TABLE IF NOT EXISTS active_players (user_id TEXT PRIMARY KEY, match_id TEXT NOT NULL)',
  );
  sql.exec(
    'CREATE TABLE IF NOT EXISTS rating_changes (id TEXT PRIMARY KEY, game_id TEXT NOT NULL, user_id TEXT NOT NULL, match_id TEXT, before INTEGER NOT NULL, after INTEGER NOT NULL, changed_at INTEGER NOT NULL, actor_id TEXT NOT NULL)',
  );
  sql.exec(
    'CREATE INDEX IF NOT EXISTS change_player ON rating_changes(game_id,user_id,changed_at DESC)',
  );
  sql.exec(
    'CREATE TABLE IF NOT EXISTS play_actions (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, reply TEXT NOT NULL, created_at INTEGER NOT NULL)',
  );
  sql.exec('CREATE INDEX IF NOT EXISTS action_age ON play_actions(created_at)');
  sql.exec(
    'CREATE TABLE IF NOT EXISTS deadlines (key TEXT PRIMARY KEY, kind TEXT NOT NULL, match_id TEXT NOT NULL, due INTEGER NOT NULL)',
  );
  sql.exec('CREATE INDEX IF NOT EXISTS deadline_due ON deadlines(due)');
  sql.exec(
    'CREATE TABLE IF NOT EXISTS cards (key TEXT PRIMARY KEY, channel_id TEXT NOT NULL, nonce TEXT NOT NULL, message_id TEXT, reconcile INTEGER NOT NULL DEFAULT 0, announced INTEGER NOT NULL DEFAULT 0)',
  );
  // Existing cards have already been announced. Only new matches should notify their roster.
  if (
    !sql
      .exec<{ name: string }>('PRAGMA table_info(cards)')
      .toArray()
      .some((c) => c.name === 'announced')
  )
    sql.exec('ALTER TABLE cards ADD COLUMN announced INTEGER NOT NULL DEFAULT 1');
  const cardColumns = new Set(
    sql
      .exec<{ name: string }>('PRAGMA table_info(cards)')
      .toArray()
      .map((c) => c.name),
  );
  if (!cardColumns.has('reconcile_cursor'))
    sql.exec('ALTER TABLE cards ADD COLUMN reconcile_cursor TEXT');
  if (!cardColumns.has('reconcile_pages'))
    sql.exec('ALTER TABLE cards ADD COLUMN reconcile_pages INTEGER NOT NULL DEFAULT 0');
  sql.exec(
    "CREATE TABLE IF NOT EXISTS outbox (key TEXT PRIMARY KEY, payload TEXT NOT NULL, version INTEGER NOT NULL, due INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'pending', error TEXT)",
  );
  sql.exec('CREATE INDEX IF NOT EXISTS outbox_due ON outbox(status,due)');
  sql.exec(
    'CREATE TABLE IF NOT EXISTS queue_clocks (queue_id TEXT NOT NULL,user_id TEXT NOT NULL,due INTEGER NOT NULL,PRIMARY KEY(queue_id,user_id))',
  );
  sql.exec('CREATE INDEX IF NOT EXISTS queue_clock_due ON queue_clocks(due)');
  sql.exec(
    'CREATE TABLE IF NOT EXISTS members (user_id TEXT PRIMARY KEY,roles TEXT NOT NULL,verified_at INTEGER NOT NULL,available INTEGER NOT NULL)',
  );
  sql.exec(
    'CREATE TABLE IF NOT EXISTS noadds (user_id TEXT PRIMARY KEY,until_ms INTEGER NOT NULL,reason TEXT NOT NULL,actor_id TEXT NOT NULL)',
  );
  sql.exec(
    'CREATE TABLE IF NOT EXISTS control_audit (id TEXT PRIMARY KEY,match_id TEXT,queue_id TEXT,action TEXT NOT NULL,actor_id TEXT NOT NULL,at INTEGER NOT NULL,details TEXT NOT NULL)',
  );
  sql.exec('CREATE INDEX IF NOT EXISTS control_match ON control_audit(match_id,at DESC)');
}
export class PlayStore {
  private flushing: Promise<void> | null = null;
  constructor(
    private ctx: DurableObjectState,
    private env: Env,
    private read: (guild: string) => Snapshot,
    private recordStore: RecordStore,
  ) {}
  private get sql() {
    return this.ctx.storage.sql;
  }
  private get ux() {
    return new UxStore(this.sql);
  }
  private get economy() {
    return new EconomyStore(this.sql);
  }
  private get civs() {
    return new CivStore(this.sql, (key, channel, card) => this.enqueue(key, channel, card));
  }
  private get lobbies() {
    return new LobbyStore(
      this.ctx,
      () => {
        const guild = this.sql
          .exec<{ guild_id: string }>('SELECT guild_id FROM community WHERE id=1')
          .toArray()[0].guild_id;
        return this.read(guild).settings;
      },
      {
        discover: async (adapterId, name) =>
          adapterId === 'aoe2'
            ? this.env.AOE2_LOBBIES.getByName(this.env.DISCORD_APPLICATION_ID ?? 'local').discover(
                name,
              )
            : { healthy: false, observations: [] },
        occupancy: async (id, name) => {
          const feed = this.env.AOE2_LOBBIES.getByName(this.env.DISCORD_APPLICATION_ID ?? 'local');
          return name
            ? ((await feed.discover(name)).observations.find((o) => o.externalId === id) ?? null)
            : (await feed.query(id)).observation;
        },
        started: (m, o, eventId) => this.economy.startObserved(m, o.startedAt!, eventId),
        match: (id) => this.match(id),
        save: (m) => this.saveMatch(m),
        audit: (action, actor, details, m) => this.audit(action, actor, details, m.id, m.queue.id),
        prompt: (m, event) =>
          this.enqueue(`end:${m.id}:${event.bindingId}`, m.queue.channelId, {
            content:
              'Game finished; declared profiles and teams match. Losing captain: report your loss below.',
            embeds: [],
            components: [
              {
                type: 1,
                components: [
                  {
                    type: 2,
                    style: 4,
                    label: 'Report loss',
                    custom_id: `m:${m.id}:${m.version}:loss`,
                  },
                ],
              },
            ],
            allowed_mentions: { parse: [] },
          }),
      },
    );
  }
  connectorEnded(guild: string, event: LobbyEvent) {
    this.read(guild);
    if (event.observation?.phase !== 'finished') return { accepted: false, reason: 'not_finished' };
    return this.lobbies.accept(guild, event);
  }
  async pollLobbies(guild: string) {
    this.read(guild);
    await this.lobbies.poll(guild);
    await this.schedule();
  }
  reconcileLobbies(gameId?: string) {
    this.ctx.storage.transactionSync(() => this.lobbies.reconcile(gameId));
  }
  goldView(
    guild: string,
    channel: string,
    actor: string,
    view = 'balance',
    queue?: string,
    admin = false,
  ) {
    const settings = this.read(guild).settings,
      q = this.queue(
        {
          id: crypto.randomUUID(),
          actor: { id: actor, name: actor },
          channelId: channel,
          admin,
          kind: 'gold',
          queue,
        },
        settings,
      );
    if (!q.modules.gold) throw new PlayError('Gold is disabled for this queue.');
    if (view === 'balance') {
      if (!this.ctx.storage.kv.get('recoveryReadOnly'))
        this.ctx.storage.transactionSync(() => this.economy.seed(q.gameId, actor));
      return `You hold ${this.economy.balance(q.gameId, actor)} gold.`;
    }
    if (view === 'history')
      return (
        this.economy
          .records(q.gameId, actor)
          .ledger.map(
            (l) =>
              `${l.kind}: ${Number(l.amount) >= 0 ? '+' : ''}${l.amount} · balance ${l.balance_after}`,
          )
          .join('\n') || 'No gold movements yet.'
      );
    const game = settings.games.find((g) => g.id === q.gameId)!;
    if (game.records?.visibility === 'admins' && !admin)
      throw new PlayError('These records are visible to administrators only.');
    if (view === 'predictions') {
      const scores = new Map<string, { wins: number; games: number }>();
      for (const row of this.sql.exec<{ user_id: string; wins: number; games: number }>(
        "SELECT b.user_id,COUNT(*) AS games,SUM(CASE WHEN b.side=p.result THEN 1 ELSE 0 END) AS wins FROM bets b JOIN books p ON p.id=b.book_id WHERE p.game_id=? AND p.status='settled' GROUP BY b.user_id",
        q.gameId,
      ))
        scores.set(row.user_id, { wins: row.wins, games: row.games });
      for (const row of this.sql.exec<{ config: string }>(
        'SELECT config FROM imported_predictions WHERE game_id=?',
        q.gameId,
      )) {
        const market = JSON.parse(row.config) as {
          status: string;
          winner: string | null;
          bets: { userId: string; side: string }[];
        };
        if (market.status !== 'settled' || !market.winner) continue;
        for (const bet of market.bets) {
          const old = scores.get(bet.userId) ?? { wins: 0, games: 0 };
          old.games++;
          if (bet.side === market.winner) old.wins++;
          scores.set(bet.userId, old);
        }
      }
      const rows = [...scores]
        .filter(
          ([user, value]) =>
            value.games >= Math.max(1, game.records?.minimumMatches ?? 1) &&
            !this.sql
              .exec<{ config: string }>(
                'SELECT config FROM profiles WHERE game_id=? AND user_id=?',
                q.gameId,
                user,
              )
              .toArray()
              .some((r) => JSON.parse(r.config).hidden),
        )
        .sort(
          (a, b) =>
            b[1].wins / b[1].games - a[1].wins / a[1].games ||
            b[1].games - a[1].games ||
            a[0].localeCompare(b[0]),
        )
        .slice(0, 20);
      return (
        `**${game.name} · predictions**\n` +
        (rows
          .map(
            ([user, s], i) =>
              `${i + 1}. <@${user}> · ${s.wins}/${s.games} correct (${Math.round((s.wins / s.games) * 100)}%)`,
          )
          .join('\n') || 'No settled predictions yet.')
      );
    }
    const rows = this.sql
      .exec<{ user_id: string; balance: number; config: string | null }>(
        'SELECT w.user_id,w.balance,p.config FROM wallets w LEFT JOIN profiles p ON p.game_id=w.game_id AND p.user_id=w.user_id WHERE w.game_id=? ORDER BY w.balance DESC LIMIT 100',
        q.gameId,
      )
      .toArray()
      .filter((r) => !r.config || !JSON.parse(r.config).hidden);
    return (
      `**${game.name} · gold**\n` +
      rows
        .slice(0, 20)
        .map((r, i) => `${i + 1}. <@${r.user_id}> · ${r.balance} gold`)
        .join('\n')
    );
  }
  configure(settings: Settings, previous?: Settings) {
    this.economy.configure(settings);
    for (const q of settings.queues) {
      if (
        previous?.queues.some((old) => old.id === q.id && JSON.stringify(old) === JSON.stringify(q))
      )
        continue;
      const current = this.sql
        .exec<{ digest: string }>('SELECT digest FROM queue_views WHERE queue_id=?', q.id)
        .toArray()[0];
      const payload = this.queuePayload(q);
      if (current && current.digest !== JSON.stringify(payload)) this.touchQueue(q);
    }
  }
  private voiceQueues(settings: Settings) {
    return settings.queues.filter(
      (q) =>
        q.enabled &&
        q.voicePreview &&
        this.sql.exec('SELECT 1 FROM cards WHERE key=?', `queue:${q.id}`).toArray().length,
    );
  }
  private visibilityQueues(settings: Settings) {
    return settings.queues.filter(
      (q) =>
        q.enabled &&
        q.keepQueueVisible !== false &&
        this.sql
          .exec(
            'SELECT 1 FROM cards WHERE key=? AND channel_id=? AND message_id IS NOT NULL',
            `queue:${q.id}`,
            q.channelId,
          )
          .toArray().length,
    );
  }
  private queueDeliveryPending(id: string) {
    // Do not reset failed/ambiguous deliveries or create overlapping publications.
    return (
      this.sql.exec('SELECT 1 FROM outbox WHERE key GLOB ? LIMIT 1', `queue:${id}:g*`).toArray()
        .length > 0
    );
  }
  private async refreshQueueVisibility(guild: string) {
    if (this.ctx.storage.kv.get('recoveryReadOnly')) return;
    const due = this.ctx.storage.kv.get<number>('queueVisibilityDue');
    if (due === undefined || due > Date.now()) return;
    // Claim this interval before I/O; other alarms/restarts never poll in a tight loop.
    this.ctx.storage.kv.put('queueVisibilityDue', Date.now() + queueVisibilityInterval);
    if (!this.env.DISCORD_BOT_TOKEN || !this.env.DISCORD_APPLICATION_ID) return;
    const queues = this.visibilityQueues(this.read(guild).settings).filter(
      (q) => !this.queueDeliveryPending(q.id),
    );
    const gate = this.env.DISCORD_GATE.getByName(this.env.DISCORD_APPLICATION_ID);
    for (const channel of new Set(queues.map((q) => q.channelId))) {
      const route = `channel:${channel}`;
      if ((await gate.reserve(route)) > Date.now()) continue;
      try {
        const response = await fetch(
          `https://discord.com/api/v10/channels/${channel}/messages?limit=5`,
          {
            headers: { Authorization: `Bot ${this.env.DISCORD_BOT_TOKEN}` },
            signal: AbortSignal.timeout(5000),
          },
        );
        if (response.status === 429) {
          const body = (await response.json().catch(() => null)) as {
            retry_after?: number;
            global?: boolean;
          } | null;
          const seconds = Number(body?.retry_after ?? response.headers.get('Retry-After') ?? 1);
          await gate.pause(
            route,
            Date.now() + (Number.isFinite(seconds) ? Math.max(1000, seconds * 1000) : 1000),
            body?.global === true,
          );
        }
        if (!response.ok) continue; // Preserve the card; retry at the next 15-minute check.
        await this.rateHeaders(response, route, gate);
        const rows: unknown = await response.json();
        if (!Array.isArray(rows) || rows.some((r) => !r || !/^\d{17,20}$/.test(r.id))) continue;
        const recent = new Set(rows.slice(0, 5).map((r) => r.id));
        // Settings/rosters may have changed while Discord was responding.
        for (const q of this.visibilityQueues(this.read(guild).settings)) {
          if (q.channelId !== channel || this.queueDeliveryPending(q.id)) continue;
          const card = this.sql
            .exec<Pointer>('SELECT * FROM cards WHERE key=?', `queue:${q.id}`)
            .toArray()[0];
          if (card.message_id && !recent.has(card.message_id)) this.touchQueue(q, true, true);
        }
      } catch {
        // A slow or unavailable Discord API must not remove a usable queue card.
      }
    }
  }
  private queuePayload(q: Queue) {
    const cache = this.ctx.storage.kv.get<QueueVoiceCache>('queueVoice');
    let voice: string[] | null = null;
    if (cache?.members && Date.now() - cache.observedAt <= voiceInterval + 30000) {
      const busy = new Set(
        this.sql
          .exec<{ user_id: string }>(
            'SELECT user_id FROM queue_entries UNION SELECT user_id FROM active_players UNION SELECT user_id FROM noadds WHERE until_ms>?',
            Date.now(),
          )
          .toArray()
          .map((p) => p.user_id),
      );
      voice = cache.members
        .filter((p) => !busy.has(p.userId))
        .slice(0, 5)
        .map((p) => p.userId);
    }
    return queueCard(
      q,
      this.entries(q.id).map((p) => ({ id: p.user_id })),
      this.ux.held(q.id),
      voice,
    );
  }
  private async refreshQueueVoice(guild: string) {
    if (this.ctx.storage.kv.get('recoveryReadOnly')) return;
    const queues = this.voiceQueues(this.read(guild).settings);
    if (!queues.length) {
      this.ctx.storage.kv.delete('queueVoice');
      return;
    }
    const previous = this.ctx.storage.kv.get<QueueVoiceCache>('queueVoice');
    if (previous && previous.due > Date.now()) return;
    let members: VoiceMember[] | null = null;
    try {
      const presence = await this.env.VOICE_PRESENCE.getByName(
        this.env.DISCORD_APPLICATION_ID!,
      ).query(guild, 'preview');
      members = presence.preview;
    } catch {
      /* Clear stale names on an unavailable feed. */
    }
    // Configuration can change during the Gateway query; never reactivate a disabled queue.
    const current = this.voiceQueues(this.read(guild).settings);
    if (!current.length) {
      this.ctx.storage.kv.delete('queueVoice');
      return;
    }
    this.ctx.storage.kv.put('queueVoice', {
      due: Date.now() + voiceInterval,
      observedAt: Date.now(),
      members,
    });
    for (const q of current) this.touchQueue(q);
  }
  private pingQueue(guild: string, a: Action) {
    if (this.ctx.storage.kv.get('recoveryReadOnly'))
      throw new PlayError('Recovery copy is read-only.');
    const q = this.queue(a, this.read(guild).settings);
    if (!q.voicePreview) throw new PlayError('Voice invitations are disabled for this queue.');
    if (!q.enabled || this.entries(q.id).length >= q.size)
      throw new PlayError('This queue does not need more players right now.');
    this.remember(a.actor);
    if (!a.admin && !this.allowed(q, a.actor.id, true))
      throw new PlayError('You are not eligible to invite players to this queue.');
    return q;
  }
  private pingCooldown() {
    const last = this.sql
      .exec<{ created_at: number }>(
        'SELECT created_at FROM rally_runs ORDER BY created_at DESC LIMIT 1',
      )
      .toArray()[0];
    if (last && last.created_at + voiceInterval > Date.now())
      throw new PlayError(
        `Players were invited recently. Try again in ${Math.ceil((last.created_at + voiceInterval - Date.now()) / 60000)} minute(s).`,
      );
  }
  private pingAvailable(q: Queue, id: string) {
    return (
      this.allowed(q, id, true) &&
      !this.sql
        .exec(
          'SELECT 1 FROM queue_entries WHERE user_id=? UNION ALL SELECT 1 FROM active_players WHERE user_id=? UNION ALL SELECT 1 FROM pending_returns WHERE user_id=? LIMIT 1',
          id,
          id,
          id,
        )
        .toArray().length
    );
  }
  async pingVoice(guild: string, a: Action) {
    const q = this.pingQueue(guild, a);
    const receipt = () => {
      const old = this.sql
        .exec<{ queue_id: string; targets: string }>(
          'SELECT queue_id,targets FROM rally_runs WHERE id=?',
          a.id,
        )
        .toArray()[0];
      if (!old) return false;
      const saved = JSON.parse(old.targets);
      if (old.queue_id !== q.id || saved.kind !== 'queue_ping' || saved.actorId !== a.actor.id)
        throw new PlayError('This invitation changed. Use the current Ping button.');
      return true;
    };
    if (receipt()) return { content: 'Invitation already accepted.' };
    this.pingCooldown();
    const feed = this.env.VOICE_PRESENCE.getByName(this.env.DISCORD_APPLICATION_ID!);
    const presence = await feed.query(guild, 'preview');
    if (!presence.preview)
      throw new PlayError('The voice list is warming up or unavailable. Retry shortly.');
    // Public voice snapshots already exclude bots. Role-gated queues also need
    // current member roles; never invite a partially verified group on API failure.
    if (q.controls?.requiredRole || q.controls?.blockedRole) {
      const gate = this.env.DISCORD_GATE.getByName(this.env.DISCORD_APPLICATION_ID!);
      const route = `members:${guild}`;
      for (const candidate of presence.preview) {
        if ((await gate.reserve(route)) > Date.now())
          throw new PlayError('Discord is busy verifying queue eligibility. Try again shortly.');
        const response = await fetch(
          `https://discord.com/api/v10/guilds/${guild}/members/${candidate.userId}`,
          {
            headers: { Authorization: `Bot ${this.env.DISCORD_BOT_TOKEN}` },
            signal: AbortSignal.timeout(1000),
          },
        );
        await this.rateHeaders(response, route, gate);
        if (response.status === 429) {
          const body = (await response.json().catch(() => null)) as {
            retry_after?: number;
            global?: boolean;
          } | null;
          const seconds = Number(body?.retry_after ?? response.headers.get('Retry-After') ?? 1);
          await gate.pause(
            route,
            Date.now() + (Number.isFinite(seconds) ? Math.max(1000, seconds * 1000) : 1000),
            body?.global === true,
          );
          throw new PlayError('Discord is busy verifying queue eligibility. Try again shortly.');
        }
        if (response.status === 404) {
          this.sql.exec('UPDATE members SET available=0 WHERE user_id=?', candidate.userId);
          continue;
        }
        if (!response.ok)
          throw new PlayError('Discord could not verify queue eligibility. Try again shortly.');
        const member = (await response.json()) as {
          roles?: string[];
          user?: { id?: string; bot?: boolean };
        };
        if (
          member.user?.id !== candidate.userId ||
          member.user.bot ||
          !Array.isArray(member.roles) ||
          member.roles.some((r) => !/^\d{17,20}$/.test(r))
        )
          throw new PlayError('Discord could not verify queue eligibility. Try again shortly.');
        this.remember({ id: candidate.userId, name: candidate.userId, roles: member.roles });
      }
    }
    const fresh = await feed.query(guild, 'preview');
    const reply = this.ctx.storage.transactionSync(() => {
      const current = this.pingQueue(guild, a);
      if (receipt()) return { content: 'Invitation already accepted.' };
      this.pingCooldown();
      if (!fresh.preview) throw new PlayError('The voice list could not be verified.');
      const targets = fresh.preview
        .filter((p) => this.pingAvailable(current, p.userId))
        .map((p) => p.userId);
      if (!targets.length)
        throw new PlayError('No eligible people in public voice channels need an invitation.');
      this.sql.exec(
        'INSERT INTO rally_runs VALUES(?,?,?,?,?)',
        a.id,
        current.id,
        Date.now(),
        JSON.stringify({ kind: 'queue_ping', actorId: a.actor.id, users: targets }),
        'pending',
      );
      // Keep each invitation under Discord's content/mention limits, including
      // communities with more candidates than the five-name preview can show.
      for (let offset = 0; offset < targets.length; offset += 50)
        this.enqueue(
          `ping:${a.id}:${offset / 50}`,
          current.channelId,
          queuePingCard(
            current,
            targets.slice(offset, offset + 50),
            current.size - this.entries(current.id).length,
          ),
        );
      this.audit('voice_ping', a.actor.id, { targets }, null, current.id);
      return { content: 'Voice invitation accepted.' };
    });
    await this.schedule();
    return reply;
  }
  private async freshPing(work: Work, guild: string): Promise<Card | null> {
    const saved = this.sql
      .exec<{ queue_id: string; created_at: number; targets: string }>(
        'SELECT queue_id,created_at,targets FROM rally_runs WHERE id=?',
        work.key.split(':')[1],
      )
      .toArray()[0];
    if (
      !saved ||
      Date.now() - saved.created_at > 30000 ||
      this.ctx.storage.kv.get('recoveryReadOnly')
    )
      return null;
    const voice = await this.env.VOICE_PRESENCE.getByName(this.env.DISCORD_APPLICATION_ID!).query(
      guild,
      'preview',
    );
    const q = this.read(guild).settings.queues.find((q) => q.id === saved.queue_id);
    if (
      !voice.preview ||
      Date.now() - saved.created_at > 30000 ||
      this.ctx.storage.kv.get('recoveryReadOnly') ||
      !q?.enabled ||
      !q.voicePreview ||
      q.channelId !==
        this.sql
          .exec<{ channel_id: string }>('SELECT channel_id FROM cards WHERE key=?', work.key)
          .toArray()[0]?.channel_id ||
      this.entries(q.id).length >= q.size
    )
      return null;
    const ids = new Set(voice.preview.map((p) => p.userId));
    const targets = (JSON.parse(work.payload) as Card).allowed_mentions.users!.filter(
      (id) => ids.has(id) && this.pingAvailable(q, id),
    );
    return targets.length ? queuePingCard(q, targets, q.size - this.entries(q.id).length) : null;
  }
  rallyQueue(guild: string, a: Action) {
    if (this.ctx.storage.kv.get('recoveryReadOnly'))
      throw new PlayError('Recovery copy is read-only.');
    const q = this.queue(a, this.read(guild).settings);
    if (!q.rally?.enabled) throw new PlayError('Voice rally is disabled for this queue.');
    if (!a.admin && !q.rally.roles.some((r) => a.actor.roles?.includes(r)))
      throw new PlayError('Only organizers or configured rally roles can invite players.');
    return q;
  }
  async rally(guild: string, a: Action, voiceChannel?: string) {
    const q = this.rallyQueue(guild, a),
      presence = await this.env.VOICE_PRESENCE.getByName(this.env.DISCORD_APPLICATION_ID!).query(
        guild,
      );
    if (!presence.members)
      throw new PlayError('The voice list is warming up or unavailable. Retry shortly.');
    const channel =
      voiceChannel ??
      presence.members.find((p) => p.userId === a.actor.id)?.channelId ??
      q.rally!.channels[0];
    if (!channel || (q.rally!.channels.length && !q.rally!.channels.includes(channel)))
      throw new PlayError('Choose an allowed voice channel for this queue.');
    const invitations = this.sql
      .exec<{ created_at: number; targets: string }>(
        'SELECT created_at,targets FROM rally_runs WHERE queue_id=? ORDER BY created_at DESC LIMIT 20',
        q.id,
      )
      .toArray();
    const lastInvite = new Map<string, number>();
    for (const r of invitations) {
      const value = JSON.parse(r.targets);
      for (const id of Array.isArray(value) ? value : value.users)
        if (!lastInvite.has(id)) lastInvite.set(id, r.created_at);
    }
    const candidates = presence.members
      .filter((p) => p.channelId === channel)
      .filter(
        (p) =>
          !this.entries(q.id).some((e) => e.user_id === p.userId) &&
          !this.sql.exec('SELECT 1 FROM active_players WHERE user_id=?', p.userId).toArray()
            .length &&
          !this.sql
            .exec('SELECT 1 FROM pending_returns WHERE queue_id=? AND user_id=?', q.id, p.userId)
            .toArray().length,
      )
      .sort(
        (a, b) =>
          (lastInvite.get(a.userId) ?? 0) - (lastInvite.get(b.userId) ?? 0) ||
          a.userId.localeCompare(b.userId),
      )
      .slice(0, 32);
    const members: Action['actor'][] = [];
    for (const candidate of candidates) {
      const gate = this.env.DISCORD_GATE.getByName(this.env.DISCORD_APPLICATION_ID!);
      const at = await gate.reserve(`members:${guild}`);
      if (at > Date.now()) break;
      try {
        const response = await fetch(
          `https://discord.com/api/v10/guilds/${guild}/members/${candidate.userId}`,
          {
            headers: { Authorization: `Bot ${this.env.DISCORD_BOT_TOKEN}` },
            signal: AbortSignal.timeout(1000),
          },
        );
        if (!response.ok) continue;
        const member = (await response.json()) as {
          roles: string[];
          user: { id: string; bot?: boolean };
        };
        if (member.user?.id !== candidate.userId || member.user.bot || !Array.isArray(member.roles))
          continue;
        members.push({ id: candidate.userId, name: candidate.userId, roles: member.roles });
        if (members.length >= q.rally!.mentionLimit) break;
      } catch {
        continue;
      }
    }
    const fresh = await this.env.VOICE_PRESENCE.getByName(this.env.DISCORD_APPLICATION_ID!).query(
      guild,
    );
    return this.ctx.storage.transaction(async () => {
      const current = this.rallyQueue(guild, a),
        now = Date.now();
      const old = this.sql
        .exec<{ targets: string }>('SELECT targets FROM rally_runs WHERE id=?', a.id)
        .toArray()[0];
      if (old) return { content: 'Invitation already accepted.' };
      if (!current.enabled || this.entries(q.id).length >= q.size)
        throw new PlayError('This queue does not need more players right now.');
      if (!fresh.members) throw new PlayError('The voice list could not be verified.');
      const last = this.sql
        .exec<{ created_at: number }>(
          'SELECT created_at FROM rally_runs WHERE queue_id=? ORDER BY created_at DESC LIMIT 1',
          q.id,
        )
        .toArray()[0];
      if (last && last.created_at + current.rally!.cooldownSeconds * 1000 > now)
        throw new PlayError('This queue was rallied recently. Try again later.');
      const selected = members
        .filter((m) => fresh.members!.some((p) => p.userId === m.id && p.channelId === channel))
        .filter((m) => {
          this.remember(m);
          return (
            this.allowed(current, m.id, true) &&
            !this.sql.exec('SELECT 1 FROM active_players WHERE user_id=?', m.id).toArray().length &&
            !this.entries(q.id).some((e) => e.user_id === m.id)
          );
        })
        .slice(0, current.rally!.mentionLimit);
      if (!selected.length)
        throw new PlayError('No eligible people in that voice channel need an invitation.');
      const targets = selected.map((m) => m.id),
        needed = Math.max(0, q.size - this.entries(q.id).length);
      this.sql.exec(
        'INSERT INTO rally_runs VALUES(?,?,?,?,?)',
        a.id,
        q.id,
        now,
        JSON.stringify({ channel, users: targets }),
        'pending',
      );
      this.enqueue(`rally:${a.id}`, q.channelId, {
        content: `${targets.map((id) => `<@${id}>`).join(' ')} — ${needed} more player${needed === 1 ? '' : 's'} needed for ${q.name}. Up for a game?`,
        embeds: [],
        components: queueCard(q, []).components.map((row) => ({
          ...row,
          components: row.components.filter((c) => c.type === 2 && c.label === 'Join'),
        })),
        allowed_mentions: { parse: [], users: targets },
      });
      await this.schedule();
      return { content: 'Voice invitation accepted.' };
    });
  }
  prediction(guild: string, channel: string, actor: string, id?: string, allowExisting = false) {
    this.read(guild);
    const m = this.selectedMatch({
      id: crypto.randomUUID(),
      actor: { id: actor, name: actor },
      channelId: channel,
      admin: false,
      kind: 'teams',
      matchId: id,
    });
    const q = this.read(guild).settings.queues.find((q) => q.id === m.queue.id);
    if ((!q?.modules.predictions || !q.modules.gold) && !allowExisting)
      throw new PlayError('Predictions are disabled for this queue.');
    if (!this.ctx.storage.kv.get('recoveryReadOnly') && q?.modules.predictions && q.modules.gold)
      this.ctx.storage.transactionSync(() => this.economy.seed(m.queue.gameId, actor));
    const book = this.economy.book(m.id);
    return {
      match: m,
      bettingEnabled: !!q?.modules.predictions && q.modules.gold,
      book,
      currentBet: book ? (this.economy.bet(book.id, actor) ?? null) : null,
      pools: book ? this.economy.pools(book.id) : { alpha: 0, beta: 0 },
      balance: this.economy.balance(m.queue.gameId, actor),
    };
  }
  predictionBook(guild: string, channel: string, actor: string, id: string, allowExisting = false) {
    const row = this.sql
      .exec<{ match_id: string }>('SELECT match_id FROM books WHERE id=?', id)
      .toArray()[0];
    if (!row) throw new PlayError('Prediction pool not found.');
    const result = this.prediction(guild, channel, actor, row.match_id, allowExisting);
    if (result.book?.id !== id || result.book.status !== 'open')
      throw new PlayError('This prediction pool is closed.');
    return result;
  }
  predictionReceipt(
    guild: string,
    input: Pick<
      Action,
      'id' | 'actor' | 'channelId' | 'admin' | 'kind' | 'bookId' | 'matchId' | 'side' | 'stake'
    >,
  ): Reply | null {
    this.read(guild);
    const row = this.sql
      .exec<{ fingerprint: string; reply: string }>(
        'SELECT fingerprint,reply FROM play_actions WHERE id=?',
        input.id,
      )
      .toArray()[0];
    if (!row) return null;
    const saved = JSON.parse(row.fingerprint) as Action;
    if (
      saved.kind !== input.kind ||
      saved.channelId !== input.channelId ||
      saved.admin !== input.admin ||
      JSON.stringify(saved.actor) !== JSON.stringify(input.actor) ||
      (input.bookId && saved.bookId !== input.bookId) ||
      (input.matchId && saved.matchId !== input.matchId) ||
      saved.side !== input.side ||
      saved.stake !== input.stake
    )
      return { content: 'This interaction was already processed with different input.', ok: false };
    return JSON.parse(row.reply) as Reply;
  }
  private entries(id: string) {
    return this.sql
      .exec<Entry>(
        'SELECT user_id,display_name FROM queue_entries WHERE queue_id=? ORDER BY seq',
        id,
      )
      .toArray();
  }
  private profile(
    gameId: string,
    person: { id: string; name: string },
    queue: Queue,
    clampDeviation = true,
  ): Player {
    const row = this.sql
      .exec<{ config: string }>(
        'SELECT config FROM profiles WHERE game_id=? AND user_id=?',
        gameId,
        person.id,
      )
      .toArray()[0];
    return row
      ? {
          ...JSON.parse(row.config),
          name: person.name,
          deviation: clampDeviation
            ? Math.min(policy(queue).deviation, JSON.parse(row.config).deviation)
            : JSON.parse(row.config).deviation,
        }
      : {
          ...person,
          rating: policy(queue).initial,
          deviation: policy(queue).deviation,
          wins: 0,
          losses: 0,
          draws: 0,
          streak: 0,
        };
  }
  private saveProfile(gameId: string, player: Player, engine: Queue['ratingEngine']) {
    this.sql.exec('INSERT OR IGNORE INTO rating_engines VALUES(?,?)', gameId, engine);
    this.sql.exec(
      'INSERT INTO profiles VALUES(?,?,?) ON CONFLICT(game_id,user_id) DO UPDATE SET config=excluded.config',
      gameId,
      player.id,
      JSON.stringify(player),
    );
    this.recordStore.changed(gameId, player.id);
  }
  private enqueue(key: string, channelId: string, payload: Card) {
    this.sql.exec(
      'INSERT OR IGNORE INTO cards(key,channel_id,nonce,announced) VALUES(?,?,?,0)',
      key,
      channelId,
      crypto.randomUUID().replaceAll('-', '').slice(0, 24),
    );
    this.sql.exec(
      "INSERT INTO outbox(key,payload,version,due) VALUES(?,?,1,?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload, version=version+1, due=excluded.due, attempts=0, status='pending', error=NULL",
      key,
      JSON.stringify(payload),
      Date.now(),
    );
  }
  private touchQueue(q: Queue, refresh = false, visibilityOnly = false, afterMatch?: string) {
    const payload = this.queuePayload(q);
    const digest = JSON.stringify(payload);
    const view = this.sql
      .exec<{ digest: string; generation: number }>(
        'SELECT * FROM queue_views WHERE queue_id=?',
        q.id,
      )
      .toArray()[0];
    const pending = view
      ? this.sql
          .exec<{ key: string; state: string; after_matches: string }>(
            'SELECT key,state,after_matches FROM queue_publications WHERE queue_id=? AND generation=?',
            q.id,
            view.generation,
          )
          .toArray()[0]
      : undefined;
    const afterMatches = [
      ...new Set<string>([
        ...(pending && pending.state !== 'sent'
          ? (JSON.parse(pending.after_matches) as string[])
          : []),
        ...(afterMatch ? [afterMatch] : []),
      ]),
    ];
    if (view?.digest === digest && !afterMatch) {
      if (!refresh) return;
      if (pending && !['sent', 'superseded'].includes(pending.state)) {
        // Recover the existing delivery, including uncertain creates, with its original nonce.
        this.sql.exec(
          "UPDATE outbox SET status='pending',attempts=0,due=? WHERE key=?",
          Date.now(),
          pending.key,
        );
        if (!visibilityOnly)
          this.sql.exec('UPDATE queue_publications SET visibility_only=0 WHERE key=?', pending.key);
        return;
      }
    }
    // Unlike publication generations, this revision also changes when multiple
    // roster updates coalesce into the same pending Discord message.
    if (view?.digest !== digest)
      this.sql.exec(
        'INSERT INTO queue_runtime(queue_id,roster_revision) VALUES(?,1) ON CONFLICT(queue_id) DO UPDATE SET roster_revision=roster_revision+1',
        q.id,
      );
    const generation =
      pending?.state === 'pending' ? view!.generation : (view?.generation ?? 0) + 1;
    const key = `queue:${q.id}:g${generation}`;
    this.sql.exec(
      'INSERT INTO queue_views(queue_id,digest,generation) VALUES(?,?,?) ON CONFLICT(queue_id) DO UPDATE SET digest=excluded.digest,generation=excluded.generation',
      q.id,
      digest,
      generation,
    );
    this.sql.exec(
      'INSERT INTO queue_publications(key,queue_id,generation,visibility_only,after_matches) VALUES(?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET visibility_only=excluded.visibility_only,after_matches=excluded.after_matches',
      key,
      q.id,
      generation,
      visibilityOnly ? 1 : 0,
      JSON.stringify(afterMatches),
    );
    for (const row of payload.components)
      for (const button of row.components)
        if (button.custom_id) button.custom_id += `:g${generation}`;
    const existingDue = this.sql
      .exec<{ due: number }>('SELECT due FROM outbox WHERE key=?', key)
      .toArray()[0]?.due;
    this.enqueue(key, q.channelId, payload);
    this.sql.exec('UPDATE outbox SET due=? WHERE key=?', existingDue ?? Date.now() + 500, key);
    this.sql.exec(
      'INSERT OR IGNORE INTO cards(key,channel_id,nonce) VALUES(?,?,?)',
      `queue:${q.id}`,
      q.channelId,
      crypto.randomUUID().replaceAll('-', '').slice(0, 24),
    );
  }
  private retireMessage(key: string, channel: string, message: string) {
    const cleanup = `retire:${key}:${message}`;
    this.enqueue(cleanup, channel, {
      content: '',
      embeds: [],
      components: [],
      allowed_mentions: { parse: [] },
    });
    this.sql.exec('UPDATE cards SET message_id=? WHERE key=?', message, cleanup);
  }
  private retireTransient(key: string) {
    const card = this.sql.exec<Pointer>('SELECT * FROM cards WHERE key=?', key).toArray()[0];
    this.sql.exec('DELETE FROM outbox WHERE key=?', key);
    if (card?.message_id) this.retireMessage(key, card.channel_id, card.message_id);
    this.sql.exec('DELETE FROM cards WHERE key=?', key);
  }
  private transientIsCurrent(key: string) {
    const [kind, id, round] = key.split(':');
    if (!['reminder', 'agreement', 'end'].includes(kind)) return true;
    const row = this.sql.exec<MatchRow>('SELECT config FROM matches WHERE id=?', id).toArray()[0];
    if (!row) return false;
    const m: Match = JSON.parse(row.config);
    if (kind === 'end')
      return (
        m.state === 'playing' &&
        m.lobby?.id === round &&
        m.lobby.state === 'observed' &&
        m.lobby.prompted &&
        m.lobby.rosterVersion === (m.rosterVersion ?? 0)
      );
    return kind === 'reminder'
      ? m.state === 'check_in' &&
          !!m.deadline &&
          m.deadline > Date.now() &&
          m.ready.length < m.roster.length &&
          (!round || round === String(m.deadline))
      : ['playing', 'check_in'].includes(m.state) && m.proposal?.id === round;
  }
  private finishReminders(m: Match) {
    for (const { key } of this.sql
      .exec<{ key: string }>(
        'SELECT key FROM cards WHERE key=? OR key LIKE ?',
        `reminder:${m.id}`,
        `reminder:${m.id}:%`,
      )
      .toArray())
      this.retireTransient(key);
  }
  private saveMatch(m: Match) {
    this.lobbies.sync(m);
    this.civs.sync(m);
    this.sql.exec(
      'INSERT INTO matches VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state,config=excluded.config',
      m.id,
      m.queue.gameId,
      m.queue.id,
      m.queue.channelId,
      m.state,
      m.createdAt,
      JSON.stringify(m),
    );
    this.recordStore.index(m);
    if (m.state !== 'check_in' || m.ready.length === m.roster.length) this.finishReminders(m);
    for (const { key } of this.sql
      .exec<{ key: string }>(
        'SELECT key FROM cards WHERE key LIKE ? OR key LIKE ?',
        `agreement:${m.id}:%`,
        `end:${m.id}:%`,
      )
      .toArray())
      if (!this.transientIsCurrent(key)) this.retireTransient(key);
    if (m.proposal && ['playing', 'check_in'].includes(m.state))
      this.enqueue(`agreement:${m.id}:${m.proposal.id}`, m.queue.channelId, agreementCard(m));
    const card = matchCard(m);
    const book = this.economy.book(m.id);
    if (
      book &&
      ['open', 'frozen'].includes(book.status) &&
      ['check_in', 'playing'].includes(m.state)
    ) {
      const pool = this.economy.pools(book.id);
      card.embeds[0].description += `\n\n**Predictions** · Alpha ${pool.alpha} · Beta ${pool.beta} gold${book.status === 'open' ? '' : ' · closed'}`;
      if (book.status === 'open')
        card.components.push({
          type: 1,
          components: [
            { type: 2, style: 3, label: 'Place bet', custom_id: `b:${m.id}:pick` },
            { type: 2, style: 2, label: 'Close betting', custom_id: `b:${m.id}:close` },
          ],
        });
    }
    if (book && ['settled', 'refunded'].includes(book.status))
      card.embeds[0].description += `\n\nPredictions ${book.status === 'settled' ? `paid for ${book.result === 'alpha' ? 'Alpha' : 'Beta'}; settlement is final.` : `refunded (${book.reason?.replaceAll('_', ' ')}).`}`;
    this.enqueue(`match:${m.id}`, m.queue.channelId, card);
  }
  private match(id: string): Match {
    const row = this.sql.exec<MatchRow>('SELECT config FROM matches WHERE id=?', id).toArray()[0];
    if (!row) throw new PlayError('Match not found in this community.');
    return JSON.parse(row.config);
  }
  private selectedMatch(a: Action): Match {
    let id = a.matchId;
    if (!id)
      id = this.sql
        .exec<{ match_id: string }>(
          'SELECT match_id FROM active_players WHERE user_id=?',
          a.actor.id,
        )
        .toArray()[0]?.match_id;
    if (!id) {
      const rows = this.sql
        .exec<{ id: string }>(
          "SELECT id FROM matches WHERE channel_id=? AND state IN ('playing','check_in') ORDER BY created_at DESC LIMIT 2",
          a.channelId,
        )
        .toArray();
      if (rows.length !== 1)
        throw new PlayError(
          rows.length ? 'Choose a match using its card.' : 'There is no active match here.',
        );
      id = rows[0].id;
    }
    const m = this.match(id);
    if (m.queue.channelId !== a.channelId)
      throw new PlayError('Use this command in the match’s channel.');
    return m;
  }
  playerMatch(
    guild: string,
    channelId: string,
    actorId: string,
    matchId?: string,
    version?: number,
  ) {
    this.read(guild);
    const m = this.selectedMatch({
      id: crypto.randomUUID(),
      actor: { id: actorId, name: 'Player' },
      kind: 'teams',
      admin: false,
      channelId,
      matchId,
    });
    if (version !== undefined && version !== m.version)
      throw new PlayError('This match changed. Use its latest card.');
    const pointer = this.sql
      .exec<Pointer>('SELECT * FROM cards WHERE key=?', `match:${m.id}`)
      .toArray()[0];
    return {
      match: m,
      url: pointer?.message_id
        ? `https://discord.com/channels/${guild}/${channelId}/${pointer.message_id}`
        : undefined,
    };
  }
  playerReceipt(
    guild: string,
    input: Pick<Action, 'id' | 'actor' | 'channelId' | 'admin' | 'kind' | 'matchId' | 'version'> & {
      targetId?: string;
    },
  ) {
    this.read(guild);
    const row = this.sql
      .exec<{ fingerprint: string; reply: string }>(
        'SELECT fingerprint,reply FROM play_actions WHERE id=?',
        input.id,
      )
      .toArray()[0];
    if (!row) return null;
    const saved: Action = JSON.parse(row.fingerprint);
    const matches =
      saved.channelId === input.channelId &&
      saved.admin === input.admin &&
      JSON.stringify(saved.actor) === JSON.stringify(input.actor) &&
      saved.kind === input.kind &&
      saved.matchId === input.matchId &&
      saved.version === input.version &&
      saved.target?.id === input.targetId;
    return matches
      ? (JSON.parse(row.reply) as Reply)
      : { content: 'This interaction was already processed with different input.', ok: false };
  }
  private queue(a: Action, settings: Settings): Queue {
    let choices = settings.queues.filter(
      (q) =>
        q.channelId === a.channelId &&
        (!a.queue ||
          q.id === a.queue ||
          [q.name, ...(q.aliases ?? [])].some((n) => n.toLowerCase() === a.queue!.toLowerCase())),
    );
    if (!a.queue && choices.length > 1) {
      const defaults = choices.filter((q) => q.isDefault);
      if (defaults.length === 1) choices = defaults;
    }
    if (choices.length !== 1)
      throw new PlayError(
        choices.length
          ? 'Choose a queue by name, or use its Join button.'
          : 'No matching queue is open here. Ask an organizer to set one up.',
      );
    return choices[0];
  }
  private audit(
    action: string,
    actor: string,
    details: unknown,
    matchId: string | null = null,
    queueId: string | null = null,
  ) {
    this.sql.exec(
      'INSERT INTO control_audit VALUES(?,?,?,?,?,?,?)',
      crypto.randomUUID(),
      matchId,
      queueId,
      action,
      actor,
      Date.now(),
      JSON.stringify(details),
    );
  }
  private remember(person: Action['actor']) {
    if (person.roles)
      this.sql.exec(
        'INSERT INTO members VALUES(?,?,?,1) ON CONFLICT(user_id) DO UPDATE SET roles=excluded.roles,verified_at=excluded.verified_at,available=1',
        person.id,
        JSON.stringify(person.roles),
        Date.now(),
      );
  }
  private allowed(q: Queue, userId: string, selecting = false) {
    const ban = this.sql
      .exec<{ until_ms: number }>('SELECT until_ms FROM noadds WHERE user_id=?', userId)
      .toArray()[0];
    if (ban && ban.until_ms > Date.now()) return false;
    const controls = q.controls;
    if (!controls?.requiredRole && !controls?.blockedRole) return true;
    const member = this.sql
      .exec<{ roles: string; verified_at: number; available: number }>(
        'SELECT * FROM members WHERE user_id=?',
        userId,
      )
      .toArray()[0];
    if (!member?.available || (selecting && member.verified_at < Date.now() - 10000)) return false;
    const guild = this.sql
      .exec<{ guild_id: string }>('SELECT guild_id FROM community WHERE id=1')
      .toArray()[0].guild_id;
    const roles = [guild, ...(JSON.parse(member.roles) as string[])];
    return (
      (!controls.requiredRole || roles.includes(controls.requiredRole)) &&
      (!controls.blockedRole || !roles.includes(controls.blockedRole))
    );
  }
  private available(q: Queue) {
    return this.entries(q.id).filter(
      (e) =>
        !this.sql.exec('SELECT 1 FROM active_players WHERE user_id=?', e.user_id).toArray()
          .length && this.allowed(q, e.user_id, true),
    );
  }
  private async refreshCandidates(guild: string, queues: Queue[]) {
    const ids = [
      ...new Set(
        queues
          .filter((q) => q.controls?.requiredRole || q.controls?.blockedRole)
          .flatMap((q) =>
            this.entries(q.id)
              .slice(0, 32)
              .map((e) => e.user_id),
          ),
      ),
    ].slice(0, 32);
    await Promise.all(
      ids.map(async (id) => {
        try {
          const gate = this.env.DISCORD_GATE.getByName(this.env.DISCORD_APPLICATION_ID!);
          const route = `members:${guild}`;
          if ((await gate.reserve(route)) > Date.now())
            throw new Error('Membership checks are rate limited.');
          const response = await fetch(
            `https://discord.com/api/v10/guilds/${guild}/members/${id}`,
            {
              headers: { Authorization: `Bot ${this.env.DISCORD_BOT_TOKEN}` },
              signal: AbortSignal.timeout(1000),
            },
          );
          await this.rateHeaders(response, route, gate);
          if (response.status === 429) {
            const limit = (await response.json().catch(() => ({}))) as {
              retry_after?: number;
              global?: boolean;
            };
            const delay =
              Number.isFinite(limit.retry_after) && limit.retry_after! > 0 ? limit.retry_after! : 1;
            await gate.pause(route, Date.now() + Math.ceil(delay * 1000), limit.global === true);
          }
          if (!response.ok) throw new Error();
          const m = (await response.json()) as {
            roles?: string[];
            user?: { id: string; bot?: boolean };
          };
          if (
            m.user?.id !== id ||
            m.user.bot ||
            !Array.isArray(m.roles) ||
            m.roles.some((r) => !/^\d{17,20}$/.test(r))
          )
            throw new Error();
          this.remember({ id, name: id, roles: m.roles });
        } catch {
          this.sql.exec(
            'INSERT INTO members VALUES(?,?,?,0) ON CONFLICT(user_id) DO UPDATE SET available=0,verified_at=excluded.verified_at',
            id,
            '[]',
            Date.now(),
          );
        }
      }),
    );
  }
  private removeWaiting(userId: string, settings: Settings) {
    this.sql.exec('DELETE FROM pending_returns WHERE user_id=?', userId);
    const affected = this.sql
      .exec<{ queue_id: string }>('SELECT queue_id FROM queue_entries WHERE user_id=?', userId)
      .toArray();
    this.sql.exec('DELETE FROM queue_entries WHERE user_id=?', userId);
    this.sql.exec('DELETE FROM queue_clocks WHERE user_id=?', userId);
    for (const row of affected) {
      const q = settings.queues.find((q) => q.id === row.queue_id);
      if (q) this.touchQueue(q);
    }
  }
  private waitingClock(q: Queue, userId: string) {
    if (q.controls?.waitingSeconds)
      this.sql.exec(
        'INSERT INTO queue_clocks VALUES(?,?,?) ON CONFLICT(queue_id,user_id) DO UPDATE SET due=excluded.due',
        q.id,
        userId,
        Date.now() + q.controls.waitingSeconds * 1000,
      );
    else this.sql.exec('DELETE FROM queue_clocks WHERE queue_id=? AND user_id=?', q.id, userId);
  }
  private armCheckIn(m: Match) {
    this.sql.exec(
      "DELETE FROM deadlines WHERE match_id=? AND kind IN ('check_in','reminder')",
      m.id,
    );
    this.finishReminders(m);
    m.deadline = Math.min(
      m.mapRound?.state === 'open' && m.mapRound.settings.countsAsCheckIn
        ? m.mapRound.closesAt
        : Date.now() + m.queue.checkIn.seconds * 1000,
      m.expiresAt ?? Infinity,
    );
    this.sql.exec(
      'INSERT INTO deadlines VALUES(?,?,?,?)',
      `close:${m.id}`,
      'check_in',
      m.id,
      m.deadline,
    );
    if (m.queue.checkIn.seconds >= 10)
      this.sql.exec(
        'INSERT INTO deadlines VALUES(?,?,?,?)',
        `remind:${m.id}`,
        'reminder',
        m.id,
        Date.now() + Math.floor(m.queue.checkIn.seconds * 500),
      );
  }
  private playing(m: Match) {
    if (m.mapRound?.state === 'open') return;
    m.state = 'playing';
    m.playingAt = Date.now();
    this.sql.exec(
      "DELETE FROM deadlines WHERE match_id=? AND kind IN ('check_in','reminder')",
      m.id,
    );
    this.finishReminders(m);
    m.deadline = null;
    if (!m.expiresAt && m.queue.controls?.matchSeconds)
      this.sql.exec(
        'INSERT INTO deadlines VALUES(?,?,?,?)',
        `lifetime:${m.id}`,
        'lifetime',
        m.id,
        m.playingAt + m.queue.controls.matchSeconds * 1000,
      );
  }
  private createMatch(
    q: Queue,
    settings: Settings,
    skip: boolean,
    chosen?: Entry[],
    manualActor?: string,
  ) {
    playable(q);
    const entries = chosen ?? this.available(q),
      count = chosen ? entries.length : Math.min(entries.length, q.size);
    const rosterSize = manualActor ? count : count - (count % 2);
    if (rosterSize < 2) throw new PlayError('At least two players are needed to start.');
    const selected = entries.slice(0, rosterSize);
    const roster = selected.map((e) =>
      this.profile(q.gameId, { id: e.user_id, name: e.display_name }, q),
    );
    const teams = balance(roster, q.teamMethod);
    const now = Date.now(),
      checking = q.checkIn.enabled && !skip;
    const m: Match = {
      id: crypto.randomUUID(),
      queue: structuredClone(q),
      gameName: settings.games.find((g) => g.id === q.gameId)!.name,
      roster,
      teams,
      captains: [teams[0][0].id, teams[1][0].id],
      ready: checking ? roster.filter((p) => this.ux.ready(q, p.id, now)).map((p) => p.id) : [],
      state: checking ? 'check_in' : 'playing',
      result: null,
      agreements: {},
      createdAt: now,
      deadline: checking ? now + q.checkIn.seconds * 1000 : null,
      closedAt: null,
      version: 1,
      changes: [],
      subRequests: [],
      generation: 0,
      replacementRounds: 0,
      behaviorVersion: 1,
      expiresAt: now + Math.min(q.controls?.matchSeconds || 43200, 43200) * 1000,
    };
    if (q.modules.maps) {
      const game = settings.games.find((g) => g.id === q.gameId)!;
      const rules = q.maps ?? (game.catalogId === 'aoe2' ? defaultMaps() : undefined);
      if (!rules) throw new PlayError('Add a map pool in queue settings before enabling maps.');
      const history = this.sql
        .exec<{ config: string }>(
          "SELECT config FROM matches WHERE queue_id=? AND state='finished' ORDER BY created_at DESC LIMIT 20",
          q.id,
        )
        .toArray()
        .slice(0, rules.recentAvoid)
        .flatMap((r) => (JSON.parse(r.config) as Match).mapRound?.selected ?? []);
      m.mapRound = createMapRound(rules, history, now);
      if (m.mapRound.state === 'open') {
        m.state = 'check_in';
        if (!checking) m.ready = roster.map((p) => p.id);
        this.sql.exec(
          'INSERT INTO deadlines VALUES(?,?,?,?)',
          `maps:${m.id}`,
          'map',
          m.id,
          Math.min(m.mapRound.closesAt, m.expiresAt!),
        );
      }
    }
    const affected = new Set<string>([q.id]);
    for (const p of roster) {
      this.sql.exec('DELETE FROM pending_returns WHERE user_id=?', p.id);
      this.sql.exec(
        'DELETE FROM readiness WHERE user_id=? AND (queue_id!=? OR game_id!=?)',
        p.id,
        q.id,
        q.gameId,
      );
      // The unique active-player row makes participation exclusion community-wide.
      this.sql.exec('INSERT INTO active_players VALUES(?,?)', p.id, m.id);
      for (const row of this.sql.exec<{ queue_id: string }>(
        'SELECT queue_id FROM queue_entries WHERE user_id=?',
        p.id,
      ))
        affected.add(row.queue_id);
      this.sql.exec('DELETE FROM queue_entries WHERE user_id=?', p.id);
      this.sql.exec('DELETE FROM queue_clocks WHERE user_id=?', p.id);
      this.saveProfile(q.gameId, p, q.ratingEngine);
    }
    this.sql.exec(
      'INSERT OR REPLACE INTO deadlines VALUES(?,?,?,?)',
      `lifetime:${m.id}`,
      'lifetime',
      m.id,
      m.expiresAt!,
    );
    if (checking && m.ready.length !== roster.length) {
      this.armCheckIn(m);
    } else this.playing(m);
    if (q.modules.stories) {
      m.storiesBefore = beforeStories(m, this.storyHistory(m));
    }
    this.economy.open(m);
    this.saveMatch(m);
    this.audit(
      'start',
      manualActor ?? 'system',
      { roster: roster.map((p) => p.id), skipCheckIn: skip },
      m.id,
      q.id,
    );
    for (const id of affected) {
      const other = settings.queues.find((q) => q.id === id);
      if (other) {
        this.sql.exec(
          "DELETE FROM outbox WHERE key IN (SELECT key FROM queue_publications WHERE queue_id=? AND state='pending')",
          id,
        );
        this.sql.exec(
          "UPDATE queue_publications SET state='superseded' WHERE queue_id=? AND state='pending'",
          id,
        );
        this.touchQueue(other, true, false, id === q.id ? m.id : undefined);
      }
    }
    return m;
  }
  private clearDeadlines(m: Match) {
    this.sql.exec('DELETE FROM deadlines WHERE match_id=?', m.id);
    this.finishReminders(m);
    m.deadline = null;
  }
  private close(m: Match, result: 'alpha' | 'beta' | 'draw' | 'abort', actor: string) {
    for (const p of m.roster) this.ux.clear(p.id);
    m.proposal = undefined;
    this.economy.finish(m, result);
    if (result === 'abort') {
      m.state = 'aborted';
      m.result = null;
    } else {
      const rated = calculate(m, result === 'draw' ? null : result === 'alpha' ? 0 : 1);
      m.ratedPlayers = rated.flat();
      for (const p of rated.flat()) {
        const before = m.roster.find((r) => r.id === p.id)!.rating;
        this.saveProfile(m.queue.gameId, p, m.queue.ratingEngine);
        this.sql.exec(
          'INSERT INTO rating_changes VALUES(?,?,?,?,?,?,?,?)',
          `${m.id}:${m.generation ?? 0}:${p.id}`,
          m.queue.gameId,
          p.id,
          m.id,
          before,
          p.rating,
          Date.now(),
          actor,
        );
        m.changes.push({ userId: p.id, before, after: p.rating });
        this.sql.exec(
          'INSERT INTO rating_details VALUES(?,?,?,?,?)',
          `${m.id}:${m.generation ?? 0}:${p.id}`,
          m.roster.find((r) => r.id === p.id)!.deviation,
          p.deviation,
          'Ranked result',
          'local',
        );
      }
      m.state = 'finished';
      m.result = result;
    }
    if (m.queue.modules.stories && m.state === 'finished')
      m.storiesAfter = afterStories(m, m.ratedPlayers ?? []);
    m.closedAt = Date.now();
    m.version++;
    this.clearDeadlines(m);
    this.sql.exec('DELETE FROM active_players WHERE match_id=?', m.id);
    this.saveMatch(m);
    this.audit(
      'report',
      actor,
      { result, generation: m.generation ?? 0, changes: m.changes },
      m.id,
      m.queue.id,
    );
  }
  private recomputeStreak(gameId: string, userId: string) {
    let streak = 0;
    let ended = false;
    const rows = this.sql.exec<{ config: string }>(
      "SELECT config FROM matches WHERE game_id=? AND state='finished' ORDER BY json_extract(config,'$.closedAt') DESC,created_at DESC,id DESC",
      gameId,
    );
    for (const row of rows) {
      const m = JSON.parse(row.config) as Match;
      const side = m.teams.findIndex((t) => t.some((p) => p.id === userId));
      if (side < 0) continue;
      const sign = m.result === 'draw' ? 0 : m.result === (side === 0 ? 'alpha' : 'beta') ? 1 : -1;
      if (!sign || (streak && Math.sign(streak) !== sign)) {
        ended = true;
        break;
      }
      streak += sign;
    }
    const base = this.sql
      .exec<{ config: string }>(
        'SELECT config FROM player_baselines WHERE game_id=? AND user_id=?',
        gameId,
        userId,
      )
      .toArray()[0];
    const value = base ? (JSON.parse(base.config) as Player).streak : 0;
    return !ended && (!streak || Math.sign(streak) === Math.sign(value)) ? streak + value : streak;
  }
  private undo(m: Match, actor: string) {
    if (m.state !== 'finished') throw new PlayError('Only a finished ranked match can be undone.');
    if (m.changes.length !== m.roster.length)
      throw new PlayError(
        'This historical record is missing original rating deltas. Import complete history before correcting it.',
      );
    for (const p of m.roster)
      if (this.sql.exec('SELECT 1 FROM active_players WHERE user_id=?', p.id).toArray().length)
        throw new PlayError(
          'Finish affected players’ active matches before correcting their ratings. Other players can continue playing.',
        );
    const previous = {
      result: m.result,
      changes: structuredClone(m.changes),
      generation: m.generation ?? 0,
    };
    m.state = 'undone';
    m.version++;
    m.closedAt = Date.now();
    // Retain the original record and deltas for audit, while excluding it from current stats.
    this.saveMatch(m);
    const rated =
      m.ratedPlayers ??
      rateTeams(
        m.teams,
        m.result === 'draw' ? null : m.result === 'alpha' ? 0 : 1,
        policy(m.queue),
        m.queue.ratingEngine,
      ).flat();
    for (const old of m.roster) {
      const current = this.profile(m.queue.gameId, old, m.queue, false);
      const delta = m.changes.find((c) => c.userId === old.id)!;
      const before = current.rating;
      current.rating = Math.max(0, current.rating - (delta.after - delta.before));
      current.deviation = Math.max(
        0,
        current.deviation -
          ((rated.find((p) => p.id === old.id)?.deviation ?? old.deviation) - old.deviation),
      );
      const side = m.teams.findIndex((t) => t.some((p) => p.id === old.id));
      const statistic =
        m.result === 'draw'
          ? 'draws'
          : m.result === (side === 0 ? 'alpha' : 'beta')
            ? 'wins'
            : 'losses';
      current[statistic] = Math.max(0, current[statistic] - 1);
      current.streak = this.recomputeStreak(m.queue.gameId, old.id);
      this.saveProfile(m.queue.gameId, current, m.queue.ratingEngine);
      this.sql.exec(
        'INSERT INTO rating_changes VALUES(?,?,?,?,?,?,?,?)',
        `undo:${m.id}:${m.generation ?? 0}:${old.id}`,
        m.queue.gameId,
        old.id,
        m.id,
        before,
        current.rating,
        Date.now(),
        actor,
      );
    }
    this.audit('undo', actor, previous, m.id, m.queue.id);
  }
  private storyHistory(m: Match) {
    return this.sql
      .exec<{ config: string }>(
        "SELECT config FROM matches WHERE game_id=? AND channel_id=? AND state='finished' AND created_at<? AND created_at>? ORDER BY created_at DESC LIMIT 5000",
        m.queue.gameId,
        m.queue.channelId,
        m.createdAt,
        m.createdAt - 90 * 86400000,
      )
      .toArray()
      .map((r) => JSON.parse(r.config) as Match);
  }
  private rosterChanged(m: Match, actor: string, details: unknown) {
    m.rosterVersion = (m.rosterVersion ?? 0) + 1;
    m.teams = m.teams.map((t) => t.slice().sort((a, b) => b.rating - a.rating)) as Match['teams'];
    if (m.teams.some((t) => !t.length) || m.roster.length > 16)
      throw new PlayError('Keep two nonempty teams with at most 16 total players.');
    m.captains = [m.teams[0][0].id, m.teams[1][0].id];
    m.agreements = {};
    m.proposal = undefined;
    this.economy.rosterChanged(m);
    if (m.queue.modules.stories) {
      m.storiesBefore = beforeStories(m, this.storyHistory(m));
    }
    if (m.mapRound?.state === 'open') {
      m.mapRound.id = crypto.randomUUID();
      m.mapRound.ballots = Object.fromEntries(
        Object.entries(m.mapRound.ballots).filter(([id]) => m.roster.some((p) => p.id === id)),
      );
      m.mapRound.closesAt = Math.min(
        Date.now() + m.mapRound.settings.seconds * 1000,
        m.expiresAt ?? Infinity,
      );
      this.sql.exec(
        'INSERT OR REPLACE INTO deadlines VALUES(?,?,?,?)',
        `maps:${m.id}`,
        'map',
        m.id,
        m.mapRound.closesAt,
      );
    }
    m.version++;
    m.subRequests = (m.subRequests ?? []).filter((id) => m.roster.some((p) => p.id === id));
    m.ready = m.ready.filter((id) => m.roster.some((p) => p.id === id));
    if (m.state === 'check_in') {
      if (m.ready.length === m.roster.length) this.playing(m);
      else this.armCheckIn(m);
    }
    this.saveMatch(m);
    this.audit('roster_changed', actor, details, m.id, m.queue.id);
  }
  private replace(
    m: Match,
    outgoing: string,
    incoming: Action['actor'],
    settings: Settings,
    actor: string,
    rebalance: boolean,
    force = false,
  ) {
    const old = m.roster.find((p) => p.id === outgoing);
    if (!old) throw new PlayError('That outgoing player is no longer in this roster.');
    if (this.sql.exec('SELECT 1 FROM active_players WHERE user_id=?', incoming.id).toArray().length)
      throw new PlayError('The replacement is already in an active match in this community.');
    if (!force && !this.allowed(m.queue, incoming.id))
      throw new PlayError('The replacement is restricted from this queue.');
    const player = this.profile(m.queue.gameId, incoming, m.queue);
    this.sql.exec('DELETE FROM active_players WHERE user_id=? AND match_id=?', outgoing, m.id);
    this.sql.exec('INSERT INTO active_players VALUES(?,?)', incoming.id, m.id);
    this.removeWaiting(incoming.id, settings);
    this.ux.clear(outgoing);
    this.saveProfile(m.queue.gameId, player, m.queue.ratingEngine);
    m.roster = m.roster.map((p) => (p.id === outgoing ? player : p));
    m.teams = rebalance
      ? balance(m.roster, 'balanced')
      : (m.teams.map((t) => t.map((p) => (p.id === outgoing ? player : p))) as Match['teams']);
    m.ready = m.ready.filter((id) => id !== outgoing);
    if (m.state === 'check_in' && this.ux.ready(m.queue, incoming.id, Date.now()))
      m.ready.push(incoming.id);
    this.rosterChanged(m, actor, { outgoing, incoming: incoming.id, rebalance, forced: force });
  }
  private expire(now: number, settings: Settings) {
    // Also retire reminders already delivered by earlier versions. Active transient
    // pointers are removed at transitions, so this bounded sweep stays small.
    for (const { key } of this.sql
      .exec<{ key: string }>(
        "SELECT key FROM cards WHERE key LIKE 'reminder:%' OR key LIKE 'agreement:%' LIMIT 100",
      )
      .toArray())
      if (!this.transientIsCurrent(key)) this.retireTransient(key);
    for (const row of this.sql
      .exec<{ queue_id: string; user_id: string }>(
        'SELECT queue_id,user_id FROM queue_clocks WHERE due<=? LIMIT 100',
        now,
      )
      .toArray()) {
      this.sql.exec(
        'DELETE FROM queue_clocks WHERE queue_id=? AND user_id=?',
        row.queue_id,
        row.user_id,
      );
      this.sql.exec(
        'DELETE FROM queue_entries WHERE queue_id=? AND user_id=?',
        row.queue_id,
        row.user_id,
      );
      this.ux.leave(row.user_id, row.queue_id);
      const q = settings.queues.find((q) => q.id === row.queue_id);
      if (q) this.touchQueue(q);
      this.audit('waiting_expired', 'system', { userId: row.user_id }, null, row.queue_id);
    }
    for (const job of this.sql
      .exec<{ key: string; kind: string; match_id: string; due: number }>(
        "SELECT key,kind,match_id,due FROM deadlines WHERE kind NOT IN ('lobby','lobby_discovery') AND due<=? ORDER BY due LIMIT 100",
        now,
      )
      .toArray()) {
      const m = this.match(job.match_id);
      const current = this.sql
        .exec<{ due: number }>('SELECT due FROM deadlines WHERE key=?', job.key)
        .toArray()[0];
      if (current?.due !== job.due) continue;
      this.sql.exec('DELETE FROM deadlines WHERE key=?', job.key);
      if (job.kind === 'civ') {
        this.civs.sync(m, now);
        continue;
      }
      if (job.kind === 'lifetime') {
        this.timeoutMatch(m, now);
        continue;
      }
      if (job.kind === 'map') {
        if (!['check_in', 'playing'].includes(m.state) || m.mapRound?.state !== 'open') continue;
        try {
          resolveMap(m.mapRound);
        } catch (error) {
          if (!(error instanceof PlayError)) throw error;
          m.note = 'Map selection ended without votes.';
          this.close(m, 'abort', 'system');
          continue;
        }
        if (!m.queue.checkIn.enabled || m.ready.length === m.roster.length) this.playing(m);
        this.saveMatch(m);
        continue;
      }
      if (m.state !== 'check_in') continue;
      if (job.kind === 'reminder' && m.deadline! > now) {
        const missing = m.roster.filter((p) => !m.ready.includes(p.id)).map((p) => p.id);
        const card = matchCard(m);
        card.content = `${missing.map((id) => `<@${id}>`).join(' ')} Check-in ends <t:${Math.floor(m.deadline! / 1000)}:R>. Confirm your place.`;
        card.embeds = [];
        if (!m.mapRound?.settings.countsAsCheckIn)
          card.components = [
            {
              type: 1,
              components: [
                { type: 2, style: 3, label: 'Check in', custom_id: `m:${m.id}:${m.version}:ready` },
              ],
            },
          ];
        card.allowed_mentions = { parse: [], users: missing };
        if (missing.length) this.enqueue(`reminder:${m.id}:${m.deadline}`, m.queue.channelId, card);
      } else if (m.deadline! <= now) {
        const q = settings.queues.find((q) => q.id === m.queue.id && q.gameId === m.queue.gameId);
        const missing = m.roster.filter((p) => !m.ready.includes(p.id));
        for (const p of missing) this.ux.clear(p.id, m.queue.id);
        const candidates = q?.enabled
          ? this.available(q).filter((e) => this.allowed(m.queue, e.user_id, true))
          : [];
        if (
          m.ready.length &&
          candidates.length >= missing.length &&
          (m.replacementRounds ?? 0) < 3
        ) {
          for (let i = 0; i < missing.length; i++)
            this.replace(
              m,
              missing[i].id,
              { id: candidates[i].user_id, name: candidates[i].display_name },
              settings,
              'system',
              false,
            );
          m.teams = balance(m.roster, m.queue.teamMethod);
          m.replacementRounds = (m.replacementRounds ?? 0) + 1;
          m.note = 'New players replaced the missing check-ins. Ready players keep their places.';
          this.rosterChanged(m, 'system', {
            reason: 'no_show_replacement',
            round: m.replacementRounds,
          });
          continue;
        }
        this.economy.finish(m, 'abort');
        m.state = 'check_in_failed';
        m.closedAt = now;
        m.version++;
        this.clearDeadlines(m);
        this.sql.exec('DELETE FROM active_players WHERE match_id=?', m.id);
        if (q) {
          let first =
            (this.sql
              .exec<{ seq: number }>(
                'SELECT MIN(seq) AS seq FROM queue_entries WHERE queue_id=?',
                q.id,
              )
              .toArray()[0]?.seq ?? 0) - m.roster.length;
          for (const p of m.roster.filter((p) => m.ready.includes(p.id))) {
            this.sql.exec(
              'INSERT OR IGNORE INTO queue_entries(seq,queue_id,user_id,display_name) VALUES(?,?,?,?)',
              first++,
              q.id,
              p.id,
              p.name,
            );
            this.waitingClock(q, p.id);
          }
          this.touchQueue(q);
        }
        this.saveMatch(m);
        this.audit(
          'check_in_failed',
          'system',
          { missing: missing.map((p) => p.id) },
          m.id,
          m.queue.id,
        );
      }
    }
  }
  private timeoutMatch(m: Match, now: number) {
    if (!['playing', 'check_in'].includes(m.state)) return;
    m.note = 'Match timed out without a result.';
    this.close(m, 'abort', 'system');
    if (m.behaviorVersion) {
      this.ux.hold(m.queue.id, true);
      for (const p of m.roster)
        this.sql.exec(
          'INSERT OR IGNORE INTO pending_returns(match_id,queue_id,user_id,name,due) VALUES(?,?,?,?,?)',
          m.id,
          m.queue.id,
          p.id,
          p.name,
          now,
        );
    }
  }
  private dispatch(a: Action, settings: Settings): Reply {
    this.remember(a.actor);
    if (a.target) this.remember(a.target);
    if (a.replacement) this.remember(a.replacement);
    if (['civ_start', 'civ_claim'].includes(a.kind)) {
      const m = this.selectedMatch(a);
      a.matchId = m.id;
      return this.civs.action(m, a, settings.games.find((g) => g.id === m.queue.gameId)?.catalogId);
    }
    if (['lobby_attach', 'lobby_unlink', 'lobby_status'].includes(a.kind))
      return this.lobbies.action(this.selectedMatch(a), a);
    if (['map_vote', 'map_pick', 'map_redo'].includes(a.kind)) {
      const m = this.selectedMatch(a),
        round = m.mapRound;
      if (!round || !['check_in', 'playing'].includes(m.state))
        throw new PlayError('Map selection is unavailable.');
      if (a.kind === 'map_redo') {
        if (!a.admin && !m.captains.includes(a.actor.id))
          throw new PlayError('Only a captain or administrator can redo maps.');
        const book = this.economy.book(m.id);
        if (book?.status === 'settled') throw new PlayError('This prediction settlement is final.');
        this.economy.rosterChanged(m);
        m.mapRound = createMapRound(round.settings, [], Date.now());
        if (m.mapRound.state === 'open') {
          m.state = 'check_in';
          m.mapRound.closesAt = Math.min(m.mapRound.closesAt, m.expiresAt ?? Infinity);
          this.sql.exec(
            'INSERT OR REPLACE INTO deadlines VALUES(?,?,?,?)',
            `maps:${m.id}`,
            'map',
            m.id,
            m.mapRound.closesAt,
          );
        }
      } else {
        if (round.id !== a.roundId || round.state !== 'open' || Date.now() >= round.closesAt)
          throw new PlayError('This map round ended. Use its current card.');
        if (a.kind === 'map_pick') {
          if (round.settings.mode !== 'direct' || (!a.admin && !m.captains.includes(a.actor.id)))
            throw new PlayError('Only a captain or administrator can pick maps.');
          if (!a.mapChoices) throw new PlayError('Choose the maps to play.');
          resolveMap(round, a.mapChoices);
        } else {
          if (!m.roster.some((p) => p.id === a.actor.id))
            throw new PlayError('Only current match players can vote.');
          voteMap(round, a.actor.id, a.mapChoices ?? []);
          if (
            round.settings.countsAsCheckIn &&
            m.queue.checkIn.enabled &&
            !m.ready.includes(a.actor.id)
          ) {
            m.ready.push(a.actor.id);
            this.ux.confirm(m.queue, a.actor.id, Date.now());
          }
          if (m.roster.every((p) => round.ballots[p.id])) resolveMap(round);
        }
      }
      if (m.mapRound!.state === 'resolved') {
        this.sql.exec('DELETE FROM deadlines WHERE key=?', `maps:${m.id}`);
        if (!m.queue.checkIn.enabled || m.ready.length === m.roster.length) this.playing(m);
      }
      this.saveMatch(m);
      return {
        content:
          m.mapRound!.state === 'resolved'
            ? `Maps selected: ${m.mapRound!.selected.join(' · ')}.`
            : m.mapRound!.settings.countsAsCheckIn && m.queue.checkIn.enabled
              ? 'Map vote saved. You’re checked in.'
              : 'Map vote saved.',
      };
    }
    if (['bet', 'bet_cancel', 'bet_close'].includes(a.kind)) {
      const m = this.selectedMatch(a),
        current = settings.queues.find((q) => q.id === m.queue.id);
      if (a.bookId && this.economy.book(m.id)?.id !== a.bookId)
        throw new PlayError('This prediction pool changed. Open Place bet again.');
      if (!['playing', 'check_in'].includes(m.state))
        throw new PlayError('This match is already closed.');
      let content: string;
      if (a.kind === 'bet') {
        if (!a.side || !a.stake) throw new PlayError('Choose a side and stake.');
        const balance = this.economy.place(
          m,
          a.actor.id,
          a.side,
          a.stake,
          a.id,
          !!current?.modules.predictions && current.modules.gold,
        );
        const total = this.economy.bet(this.economy.book(m.id)!.id, a.actor.id)!.stake;
        content = `Added ${a.stake} gold to ${a.side === 'alpha' ? 'Alpha' : 'Beta'}. Your bet: ${total} gold. You hold ${balance} gold.`;
      } else if (a.kind === 'bet_cancel') {
        this.economy.cancel(m, a.actor.id, a.id);
        content = 'Bet cancelled. Your gold was returned.';
      } else {
        if (!a.admin && !m.captains.includes(a.actor.id))
          throw new PlayError('Only a captain or administrator can close betting.');
        this.economy.freeze(m);
        content = 'Betting closed.';
      }
      this.saveMatch(m);
      return { content };
    }
    if (a.kind === 'gold') {
      const q = this.queue(a, settings);
      if (!q.modules.gold) throw new PlayError('Gold is disabled for this queue.');
      this.economy.seed(q.gameId, a.actor.id);
      return { content: `You hold ${this.economy.balance(q.gameId, a.actor.id)} gold.` };
    }
    const control = this.control(a, settings);
    if (control) return control;
    const adminKinds = ['seed', 'recover_abort'];
    if (adminKinds.includes(a.kind) && !a.admin)
      throw new PlayError('Manage Server permission is required for this action.');
    if (a.target && a.target.id !== a.actor.id && !a.admin && ['add', 'remove'].includes(a.kind))
      throw new PlayError('Only an administrator can add or remove another player.');
    if (['ready', 'loss', 'draw', 'abort', 'recover_abort', 'teams'].includes(a.kind)) {
      const m = this.selectedMatch(a);
      if (a.kind === 'teams') {
        this.saveMatch(m);
        return {
          content: `${m.queue.name}: see the match card for your teams and captains.`,
        };
      }
      if (a.version !== undefined && a.version !== m.version)
        throw new PlayError('This match changed. Use its latest card.');
      if (['finished', 'aborted', 'check_in_failed'].includes(m.state))
        throw new PlayError('This match is already closed.');
      if (a.kind === 'recover_abort') {
        this.close(m, 'abort', a.actor.id);
        return {
          content: 'Match cancelled. No ratings changed.',
        };
      }
      if (a.kind === 'ready') {
        if (m.state !== 'check_in') throw new PlayError('Check-in is closed.');
        if (!m.roster.some((p) => p.id === a.actor.id))
          throw new PlayError('Only selected match players can check in.');
        if (m.mapRound?.state === 'open' && m.mapRound.settings.countsAsCheckIn)
          throw new PlayError('Cast your map vote to check in.');
        if (!m.ready.includes(a.actor.id)) {
          m.ready.push(a.actor.id);
          this.ux.confirm(m.queue, a.actor.id, Date.now());
        }
        if (m.ready.length === m.roster.length) {
          m.version++;
          this.playing(m);
        }
        this.saveMatch(m);
        return {
          content:
            m.ready.length === m.roster.length
              ? m.mapRound?.state === 'open'
                ? 'Everyone is checked in. Finish map selection below.'
                : 'Everyone is ready. Your teams are set.'
              : (m.queue.checkIn.reuseSeconds ?? 900) > 0
                ? `Checked in. You’ll stay ready for ${Math.ceil((m.queue.checkIn.reuseSeconds ?? 900) / 60)} minutes while waiting for this game.`
                : 'Checked in.',
        };
      }
      if (a.kind === 'abort' && a.admin) {
        this.close(m, 'abort', a.actor.id);
        return { content: 'Match cancelled. No ratings changed.' };
      }
      if (m.state !== 'playing' && a.kind !== 'abort')
        throw new PlayError(
          'Finish check-in before reporting a result. An organizer can cancel the match.',
        );
      const side = m.captains.indexOf(a.actor.id);
      if (side < 0)
        throw new PlayError(
          'Only a team captain can report. Ask your captain to use the match card.',
        );
      if (a.kind === 'loss') {
        this.close(m, side === 0 ? 'beta' : 'alpha', a.actor.id);
        return {
          content: `${side === 0 ? 'Beta' : 'Alpha'} wins. Result and ratings saved.`,
        };
      }
      const vote = a.kind as 'draw' | 'abort';
      if (a.proposalId && (m.proposal?.id !== a.proposalId || m.proposal.opposing !== a.actor.id))
        throw new PlayError('That request changed. Use the latest match card.');
      if (!m.proposal || m.proposal.kind !== vote) {
        m.agreements = {};
        m.proposal = {
          id: crypto.randomUUID(),
          requester: a.actor.id,
          kind: vote,
          opposing: m.captains[side === 0 ? 1 : 0],
        };
      }
      m.agreements[a.actor.id] = vote;
      if (m.captains.every((id) => m.agreements[id] === vote)) {
        this.close(m, vote, a.actor.id);
        return {
          content:
            vote === 'draw'
              ? 'Draw recorded. Ratings updated.'
              : 'Match cancelled. No ratings changed.',
        };
      }
      this.saveMatch(m);
      return {
        content: `Waiting for the other captain to ${vote === 'draw' ? 'confirm the draw' : 'cancel the match'} on the match card.`,
      };
    }
    const q = this.queue(a, settings),
      target = a.target ?? a.actor;
    if (a.kind === 'rank') {
      const p = this.profile(q.gameId, target, q);
      return {
        content: `**${p.name.replace(/[\n`*_~<>]/g, '')}** · ${settings.games.find((g) => g.id === q.gameId)!.name}\nRating ${p.rating} · ${p.wins} wins / ${p.losses} losses / ${p.draws} draws · streak ${p.streak}.`,
      };
    }
    if (a.kind === 'seed') {
      if (a.rating === undefined) throw new PlayError('Specify a rating.');
      playable(q);
      if (this.sql.exec('SELECT 1 FROM active_players WHERE user_id=?', target.id).toArray().length)
        throw new PlayError(
          'Finish or abort this player’s active match before changing their rating.',
        );
      const p = this.profile(q.gameId, target, q),
        before = p.rating;
      p.rating = a.rating;
      if (a.deviation !== undefined) p.deviation = a.deviation;
      this.saveProfile(q.gameId, p, q.ratingEngine);
      this.sql.exec(
        'INSERT INTO rating_changes VALUES(?,?,?,?,?,?,?,?)',
        `seed:${a.id}`,
        q.gameId,
        target.id,
        null,
        before,
        p.rating,
        Date.now(),
        a.actor.id,
      );
      return {
        content: `Seeded ${p.name.replace(/[\n`*_~<>]/g, '')}: ${before} → ${p.rating}. This affects only this community and game.`,
      };
    }
    if (a.kind === 'queue') {
      this.touchQueue(q, true);
      return {
        content: `${q.name} · ${this.entries(q.id).length}/${q.size} waiting. See the queue card to join or leave.`,
      };
    }
    if (a.kind === 'remove') {
      this.ux.leave(target.id, q.id);
      const removed = this.sql
        .exec(
          'DELETE FROM queue_entries WHERE queue_id=? AND user_id=? RETURNING user_id',
          q.id,
          target.id,
        )
        .toArray().length;
      this.sql.exec('DELETE FROM queue_clocks WHERE queue_id=? AND user_id=?', q.id, target.id);
      this.touchQueue(q);
      return {
        content: removed
          ? target.id === a.actor.id
            ? `Left ${q.name}.`
            : `Removed <@${target.id}> from ${q.name}.`
          : target.id === a.actor.id
            ? `You’re not waiting in ${q.name}. If you’re in a match, use its controls.`
            : `<@${target.id}> is not waiting in ${q.name}.`,
      };
    }
    if (!q.enabled)
      throw new PlayError(`${q.name} is closed. Ask an organizer when it will reopen.`);
    playable(q);
    if (a.kind === 'start') {
      if (a.skipCheckIn && !a.admin)
        throw new PlayError(
          'Only an administrator can skip check-in. Use /queue start with the saved rules.',
        );
      this.ux.hold(q.id, false);
      const m = this.createMatch(q, settings, a.skipCheckIn ?? false, undefined, a.actor.id);
      return {
        content: `${q.name} match created. ${m.mapRound?.state === 'open' ? 'Choose the maps on the match card.' : m.state === 'check_in' ? 'Check in on the match card.' : 'Teams are ready to play.'}`,
      };
    }
    if (this.sql.exec('SELECT 1 FROM active_players WHERE user_id=?', target.id).toArray().length)
      throw new PlayError(
        target.id === a.actor.id
          ? 'You’re already in an active match here. Finish it before joining another queue.'
          : `<@${target.id}> is already in an active match here. Finish it before joining another queue.`,
      );
    if (!this.allowed(q, target.id))
      throw new PlayError(
        target.id === a.actor.id
          ? `You can’t join ${q.name} right now. Ask an organizer to check your access.`
          : `<@${target.id}> can’t join ${q.name} right now. Check their access and queue restrictions.`,
      );
    const waiting = this.entries(q.id);
    const alreadyWaiting = waiting.some((p) => p.user_id === target.id);
    if (alreadyWaiting && this.ux.held(q.id)) {
      this.ux.hold(q.id, false);
      if ((q.autoStart ?? true) && this.available(q).length >= q.size) {
        const m = this.createMatch(q, settings, false);
        return {
          content: `${q.name}: match found. ${m.mapRound?.state === 'open' ? 'Choose the maps on the match card.' : m.state === 'check_in' ? 'Check in on the match card.' : 'Your teams are ready on the match card.'}`,
        };
      }
      this.touchQueue(q);
      return { content: `${q.name} is ready to form the next match.` };
    }
    if (!alreadyWaiting && waiting.length >= ((q.autoStart ?? true) ? q.size : 128))
      throw new PlayError(`${q.name} is full. Wait for a place to open.`);
    const added = this.sql
      .exec(
        'INSERT OR IGNORE INTO queue_entries(queue_id,user_id,display_name) VALUES(?,?,?) RETURNING user_id',
        q.id,
        target.id,
        target.name,
      )
      .toArray().length;
    this.waitingClock(q, target.id);
    if (added) {
      this.ux.leave(target.id, q.id);
      this.ux.hold(q.id, false);
    }
    const due = this.sql
      .exec<{ due: number }>(
        'SELECT due FROM queue_clocks WHERE queue_id=? AND user_id=?',
        q.id,
        target.id,
      )
      .toArray()[0]?.due;
    const expiry =
      due && target.id === a.actor.id ? ` Your place expires <t:${Math.floor(due / 1000)}:R>.` : '';
    if (
      !added &&
      (q.autoStart ?? true) &&
      !this.ux.held(q.id) &&
      this.available(q).length >= q.size
    ) {
      const m = this.createMatch(q, settings, false);
      return {
        content: `${q.name}: match found. ${m.mapRound?.state === 'open' ? 'Choose the maps on the match card.' : m.state === 'check_in' ? 'Check in on the match card.' : 'Your teams are ready on the match card.'}`,
      };
    }
    if (!added)
      return {
        content:
          target.id === a.actor.id
            ? `You’re already waiting in ${q.name}.${expiry}`
            : `<@${target.id}> is already waiting in ${q.name}.`,
      };
    this.saveProfile(q.gameId, this.profile(q.gameId, target, q), q.ratingEngine);
    if ((q.autoStart ?? true) && this.available(q).length >= q.size) {
      const m = this.createMatch(q, settings, false);
      return {
        content: `${q.name}: match found. ${m.mapRound?.state === 'open' ? 'Choose the maps on the match card.' : m.state === 'check_in' ? 'Check in on the match card.' : 'Your teams are ready on the match card.'}`,
      };
    }
    this.touchQueue(q);
    return {
      content: `${target.id === a.actor.id ? 'Joined' : `Added <@${target.id}> to`} ${q.name} · ${this.entries(q.id).length}/${q.size}.${expiry}`,
    };
  }
  private control(a: Action, settings: Settings): Reply | null {
    const privileged = [
      'force_sub',
      'put',
      'clear',
      'split',
      'admin_report',
      'manual_create',
      'undo',
      'recover',
      'noadd',
      'forgive',
    ];
    if (privileged.includes(a.kind) && !a.admin)
      throw new PlayError('Manage Server permission is required for this action.');
    if (
      [
        'subme',
        'subfor',
        'subauto',
        'force_sub',
        'put',
        'back',
        'admin_report',
        'undo',
        'recover',
      ].includes(a.kind)
    ) {
      const outgoing = a.target ?? a.actor;
      const targetId = ['subfor', 'subauto', 'force_sub'].includes(a.kind)
        ? this.sql
            .exec<{ match_id: string }>(
              'SELECT match_id FROM active_players WHERE user_id=?',
              outgoing.id,
            )
            .toArray()[0]?.match_id
        : undefined;
      const m = this.selectedMatch({ ...a, matchId: a.matchId ?? targetId });
      if (a.version !== undefined && a.version !== m.version)
        throw new PlayError('This match changed. Use its latest card.');
      if (a.kind === 'recover') {
        this.saveMatch(m);
        this.audit('card_recovery', a.actor.id, { state: m.state }, m.id, m.queue.id);
        return {
          content: `Saved match ${m.id} (${m.state}) will be refreshed. Deleted cards are recreated; ratings are unchanged.`,
        };
      }
      if (a.kind === 'undo') {
        this.undo(m, a.actor.id);
        return {
          content: `Match ${m.id} undone. Recorded rating deltas and match counts reversed; audit retained. Use /manage match report to record the corrected result.`,
        };
      }
      if (a.kind === 'admin_report') {
        if (!a.result) throw new PlayError('Choose Alpha, Beta, draw or abort.');
        if (m.state === 'finished')
          throw new PlayError(
            'This match is already finished. Undo it first, then report the corrected result.',
          );
        if (!['check_in', 'playing', 'undone'].includes(m.state))
          throw new PlayError('This match is already closed.');
        if (m.state === 'undone') {
          for (const p of m.roster)
            if (
              this.sql.exec('SELECT 1 FROM active_players WHERE user_id=?', p.id).toArray().length
            )
              throw new PlayError(
                'Finish affected players’ active matches before re-reporting this record.',
              );
          m.teams = m.teams.map((t) =>
            t.map((p) => this.profile(m.queue.gameId, p, m.queue)),
          ) as Match['teams'];
          m.roster = m.teams.flat();
          m.generation = (m.generation ?? 0) + 1;
          m.changes = [];
          m.ratedPlayers = undefined;
        }
        this.close(m, a.result, a.actor.id);
        return {
          content: `Administrator recorded ${a.result} for match ${m.id}. Ratings and web history updated once.`,
        };
      }
      if (!['check_in', 'playing'].includes(m.state))
        throw new PlayError('This match is already closed.');
      if (a.kind === 'subme') {
        if (!m.roster.some((p) => p.id === a.actor.id))
          throw new PlayError('Only a player in this match can request a replacement.');
        const requested = (m.subRequests ?? []).includes(a.actor.id);
        const desired = a.subRequest ?? !requested;
        if (desired === requested)
          return {
            content: requested
              ? 'You’re already looking for a substitute.'
              : 'Your substitute request is already cancelled.',
          };
        m.subRequests = !desired
          ? m.subRequests!.filter((id) => id !== a.actor.id)
          : [...(m.subRequests ?? []), a.actor.id];
        this.saveMatch(m);
        this.audit('sub_request', a.actor.id, { requested: desired }, m.id, m.queue.id);
        return {
          content: desired
            ? 'Substitute requested. Your place stays reserved until someone takes over.'
            : 'Substitute request cancelled. You’re still in the match.',
        };
      }
      if (a.kind === 'put') {
        if (!a.target || !a.side) throw new PlayError('Choose a player and Alpha or Beta.');
        let player = m.roster.find((p) => p.id === a.target!.id);
        if (!player) {
          if (
            this.sql.exec('SELECT 1 FROM active_players WHERE user_id=?', a.target.id).toArray()
              .length
          )
            throw new PlayError('That player is already in an active match in this community.');
          player = this.profile(m.queue.gameId, a.target, m.queue);
          m.roster.push(player);
          this.sql.exec('INSERT INTO active_players VALUES(?,?)', player.id, m.id);
          this.removeWaiting(player.id, settings);
          this.saveProfile(m.queue.gameId, player, m.queue.ratingEngine);
        }
        m.teams = m.teams.map((t) => t.filter((p) => p.id !== player!.id)) as Match['teams'];
        m.teams[a.side === 'alpha' ? 0 : 1].push(player);
        this.rosterChanged(m, a.actor.id, { placed: player.id, side: a.side });
        return { content: `Player placed on ${a.side}. Teams and captains refreshed.` };
      }
      if (a.kind === 'back' && m.state !== 'check_in')
        throw new PlayError('The match has started. Request a substitute from its card.');
      if (a.kind === 'subauto' || a.kind === 'back') {
        if (
          outgoing.id !== a.actor.id &&
          !a.admin &&
          !m.captains.includes(a.actor.id) &&
          !(m.subRequests ?? []).includes(outgoing.id)
        )
          throw new PlayError('Ask that player or a captain to request the replacement.');
        if (!m.roster.some((p) => p.id === outgoing.id))
          throw new PlayError('That outgoing player is no longer in this roster.');
        const q = settings.queues.find((q) => q.id === m.queue.id);
        const candidate = q?.enabled
          ? this.available(q).find((e) => this.allowed(m.queue, e.user_id, true))
          : undefined;
        if (!candidate) {
          if (a.kind !== 'back')
            throw new PlayError(
              'No eligible substitute is waiting. Ask someone to join the queue or take your place.',
            );
          m.ready = m.ready.filter((id) => id !== outgoing.id);
          m.deadline = Date.now();
          this.saveMatch(m);
          this.sql.exec(
            'INSERT OR REPLACE INTO deadlines VALUES(?,?,?,?)',
            `close:${m.id}`,
            'check_in',
            m.id,
            m.deadline,
          );
          this.expire(Date.now(), settings);
          return {
            content: 'You left check-in. Ready players returned to the queue; no ratings changed.',
          };
        }
        this.replace(
          m,
          outgoing.id,
          { id: candidate.user_id, name: candidate.display_name },
          settings,
          a.actor.id,
          true,
        );
        return {
          content: `<@${candidate.user_id}> replaced <@${outgoing.id}>. Teams rebalanced${m.state === 'check_in' ? '; the substitute must check in' : ''}. Check the updated teams on the match card.`,
        };
      }
      if (a.kind === 'subfor' || a.kind === 'force_sub') {
        if (!a.target) throw new PlayError('Choose the outgoing player.');
        if (a.kind === 'subfor' && !(m.subRequests ?? []).includes(outgoing.id))
          throw new PlayError(
            'That player hasn’t requested a substitute. Ask them to request one on the match card.',
          );
        const incoming = a.kind === 'force_sub' ? a.replacement : a.actor;
        if (!incoming) throw new PlayError('Choose a replacement player.');
        this.replace(m, outgoing.id, incoming, settings, a.actor.id, false, a.kind === 'force_sub');
        return {
          content: `<@${incoming.id}> replaced <@${outgoing.id}> on ${m.teams[0].some((p) => p.id === incoming.id) ? 'Alpha' : 'Beta'}${m.state === 'check_in' ? '. The substitute must check in' : ''}. Everyone else keeps their team.`,
        };
      }
    }
    if (['clear', 'split', 'noadd', 'forgive', 'manual_create'].includes(a.kind)) {
      const q = this.queue(a, settings);
      if (a.kind === 'clear') {
        const count = this.entries(q.id).length;
        this.sql.exec('DELETE FROM queue_entries WHERE queue_id=?', q.id);
        this.sql.exec('DELETE FROM queue_clocks WHERE queue_id=?', q.id);
        this.sql.exec('DELETE FROM readiness WHERE queue_id=?', q.id);
        this.sql.exec('DELETE FROM pending_returns WHERE queue_id=?', q.id);
        this.ux.hold(q.id, false);
        this.touchQueue(q);
        this.audit('queue_clear', a.actor.id, { removed: count }, null, q.id);
        return {
          content: `Cleared ${count} waiting player(s) from ${q.name}. Active matches unchanged.`,
        };
      }
      if (a.kind === 'noadd' || a.kind === 'forgive') {
        if (!a.target || (a.kind === 'noadd' && !a.seconds))
          throw new PlayError('Choose a player and a no-add duration in seconds.');
        if (a.kind === 'noadd') {
          this.sql.exec(
            'INSERT INTO noadds VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET until_ms=excluded.until_ms,reason=excluded.reason,actor_id=excluded.actor_id',
            a.target.id,
            Date.now() + a.seconds! * 1000,
            a.reason ?? '',
            a.actor.id,
          );
          this.removeWaiting(a.target.id, settings);
          this.ux.leave(a.target.id);
        } else this.sql.exec('DELETE FROM noadds WHERE user_id=?', a.target.id);
        this.audit(
          a.kind,
          a.actor.id,
          { userId: a.target.id, seconds: a.seconds, reason: a.reason },
          null,
          q.id,
        );
        return {
          content:
            a.kind === 'noadd'
              ? `No-add restriction applied for ${a.seconds} seconds within this community. Waiting entries removed; active matches can finish.`
              : 'No-add restriction removed. The player can /add again if queue roles allow.',
        };
      }
      if (!q.enabled) throw new PlayError('Enable this queue before creating matches.');
      playable(q);
      if (a.kind === 'split') {
        let candidates = this.available(q);
        const size =
          a.groupSize ?? Math.min(16, Math.max(2, Math.floor(candidates.length / 2 / 2) * 2));
        if (size % 2 || size > 16) throw new PlayError('Choose an even group size from 2–16.');
        if (candidates.length < size)
          throw new PlayError('Not enough eligible waiting players for this group size.');
        if (a.sortByRating)
          candidates = candidates
            .slice()
            .sort(
              (x, y) =>
                this.profile(q.gameId, { id: y.user_id, name: y.display_name }, q).rating -
                this.profile(q.gameId, { id: x.user_id, name: x.display_name }, q).rating,
            );
        const matches: string[] = [];
        for (let i = 0; i + size <= candidates.length; i += size)
          matches.push(this.createMatch(q, settings, false, candidates.slice(i, i + size)).id);
        this.audit(
          'queue_split',
          a.actor.id,
          { size, sortByRating: a.sortByRating ?? false, matches },
          null,
          q.id,
        );
        return {
          content: `Created ${matches.length} match(es) of ${size} players: ${matches.join(', ')}. Leftovers remain queued.`,
        };
      }
      if (a.kind === 'manual_create') {
        if (!a.teams || !a.result || a.result === 'abort')
          throw new PlayError(
            'Provide two nonempty teams and Alpha, Beta or draw as the played result.',
          );
        const ids = a.teams.flat().map((p) => p.id);
        if (new Set(ids).size !== ids.length)
          throw new PlayError('Teams cannot contain duplicate players.');
        for (const id of ids)
          if (this.sql.exec('SELECT 1 FROM active_players WHERE user_id=?', id).toArray().length)
            throw new PlayError(
              'Finish these players’ active matches before creating a manual result.',
            );
        const teams = a.teams.map((t) =>
          t.map((p) => this.profile(q.gameId, p, q)).sort((a, b) => b.rating - a.rating),
        ) as Match['teams'];
        const m: Match = {
          id: crypto.randomUUID(),
          queue: structuredClone(q),
          gameName: settings.games.find((g) => g.id === q.gameId)!.name,
          roster: teams.flat(),
          teams,
          captains: [teams[0][0].id, teams[1][0].id],
          ready: [],
          state: 'playing',
          result: null,
          agreements: {},
          createdAt: Date.now(),
          deadline: null,
          closedAt: null,
          version: 1,
          changes: [],
          generation: 0,
          subRequests: [],
          note: 'Manually recorded game.',
        };
        this.close(m, a.result, a.actor.id);
        this.audit('manual_create', a.actor.id, { teams: ids, result: a.result }, m.id, q.id);
        return {
          content: `Manual ranked match ${m.id} recorded (${a.result}). Ratings and history updated.`,
        };
      }
    }
    return null;
  }
  private async returnPlayers(guild: string) {
    const rows = this.sql
      .exec<{
        seq: number;
        queue_id: string;
        user_id: string;
        name: string;
        attempts: number;
        status: string;
      }>(
        "SELECT * FROM pending_returns WHERE (status='pending' AND due<=?) OR status='overflow' ORDER BY seq LIMIT 16",
        Date.now(),
      )
      .toArray();
    for (const row of rows) {
      const q = this.read(guild).settings.queues.find((q) => q.id === row.queue_id);
      if (
        !q ||
        this.sql.exec('SELECT 1 FROM active_players WHERE user_id=?', row.user_id).toArray().length
      ) {
        this.sql.exec('DELETE FROM pending_returns WHERE seq=?', row.seq);
        continue;
      }
      if (this.entries(q.id).length >= ((q.autoStart ?? true) ? q.size : 128)) {
        this.sql.exec("UPDATE pending_returns SET status='overflow' WHERE seq=?", row.seq);
        continue;
      }
      try {
        const gate = this.env.DISCORD_GATE.getByName(this.env.DISCORD_APPLICATION_ID!);
        const at = await gate.reserve(`members:${guild}`);
        if (at > Date.now()) {
          this.sql.exec(
            "UPDATE pending_returns SET status='pending',due=? WHERE seq=?",
            at,
            row.seq,
          );
          continue;
        }
        const response = await fetch(
          `https://discord.com/api/v10/guilds/${guild}/members/${row.user_id}`,
          {
            headers: { Authorization: `Bot ${this.env.DISCORD_BOT_TOKEN}` },
            signal: AbortSignal.timeout(1000),
          },
        );
        if (response.status === 404) {
          this.sql.exec('DELETE FROM pending_returns WHERE seq=?', row.seq);
          continue;
        }
        if (!response.ok) throw new Error('Membership unavailable');
        const member = (await response.json()) as {
          roles: string[];
          user: { id: string; bot?: boolean };
        };
        if (member.user?.id !== row.user_id || member.user.bot || !Array.isArray(member.roles))
          throw new Error('Membership unavailable');
        this.ctx.storage.transactionSync(() => {
          if (!this.sql.exec('SELECT 1 FROM pending_returns WHERE seq=?', row.seq).toArray().length)
            return;
          const current = this.read(guild).settings.queues.find((q) => q.id === row.queue_id);
          if (
            !current ||
            this.sql.exec('SELECT 1 FROM active_players WHERE user_id=?', row.user_id).toArray()
              .length
          ) {
            this.sql.exec('DELETE FROM pending_returns WHERE seq=?', row.seq);
            return;
          }
          this.remember({ id: row.user_id, name: row.name, roles: member.roles });
          if (!this.allowed(current, row.user_id, true)) {
            this.audit('return_ineligible', 'system', { userId: row.user_id }, null, current.id);
            this.sql.exec('DELETE FROM pending_returns WHERE seq=?', row.seq);
            return;
          }
          if (
            this.entries(current.id).length >= ((current.autoStart ?? true) ? current.size : 128)
          ) {
            this.sql.exec("UPDATE pending_returns SET status='overflow' WHERE seq=?", row.seq);
            return;
          }
          this.sql.exec(
            'INSERT OR IGNORE INTO queue_entries(queue_id,user_id,display_name) VALUES(?,?,?)',
            current.id,
            row.user_id,
            row.name,
          );
          this.waitingClock(current, row.user_id);
          this.sql.exec('DELETE FROM pending_returns WHERE seq=?', row.seq);
          this.touchQueue(current);
        });
      } catch {
        this.sql.exec(
          'UPDATE pending_returns SET attempts=attempts+1,status=?,due=? WHERE seq=?',
          row.attempts >= 9 ? 'blocked' : 'pending',
          Date.now() + Math.min(900000, 1000 * 2 ** (row.attempts + 1)),
          row.seq,
        );
      }
    }
  }
  private adminView(guild: string, request: AdminRequest): AdminView {
    if (this.ctx.storage.kv.get('recoveryReadOnly'))
      throw new PlayError('Recovery copy is read-only.');
    const queues = this.read(guild).settings.queues.filter(
      (q) => q.channelId === request.channelId,
    );
    if (!queues.length) throw new PlayError('No queues are configured in this channel.');
    const selected = request.matchId ? this.match(request.matchId) : null;
    const name = request.queue?.toLowerCase();
    const queue = selected
      ? queues.find(
          (q) => q.id === selected.queue.id && selected.queue.channelId === request.channelId,
        )
      : name
        ? queues.find((q) =>
            [q.id, q.name, ...(q.aliases ?? [])].some((value) => value.toLowerCase() === name),
          )
        : (queues.find((q) => q.isDefault) ?? queues[0]);
    if (!queue) throw new PlayError('Choose a queue in this channel.');
    const matches = (filter: string, limit: number) =>
      this.sql
        .exec<MatchRow>(
          `SELECT config FROM matches WHERE queue_id=? AND channel_id=? ${filter} ORDER BY created_at DESC,id DESC LIMIT ?`,
          queue.id,
          request.channelId,
          limit,
        )
        .toArray()
        .map((row) => JSON.parse(row.config) as Match);
    const active = matches("AND state IN ('check_in','playing')", 25);
    return {
      queue,
      canModerate: adminAllowed(queue, request),
      queues: queues.map((q) => ({ id: q.id, name: q.name })),
      waiting: this.entries(queue.id).length,
      held: this.ux.held(queue.id),
      queueVersion:
        this.sql
          .exec<{ roster_revision: number }>(
            'SELECT roster_revision FROM queue_runtime WHERE queue_id=?',
            queue.id,
          )
          .toArray()[0]?.roster_revision ?? 0,
      latest: matches('', 1)[0] ?? null,
      recent: matches("AND state NOT IN ('check_in','playing')", 5),
      active,
      selected: selected ?? active[0] ?? null,
    };
  }
  async adminPanel(guild: string, request: AdminRequest) {
    const view = this.adminView(guild, request);
    if (['view', 'match'].includes(request.op)) return { view };
    const matchOps = ['alpha_loss', 'beta_loss', 'draw', 'cancel', 'recover', 'close_betting'];
    const validate = () => {
      const current = this.adminView(guild, request); // Recheck current role configuration after I/O.
      const ownLoss =
        (request.op === 'alpha_loss' && current.selected?.captains[0] === request.actor.id) ||
        (request.op === 'beta_loss' && current.selected?.captains[1] === request.actor.id);
      if (!['start', 'queue', 'recover'].includes(request.op) && !current.canModerate && !ownLoss)
        throw new PlayError(
          'Administrator, Manage Server, or a configured bot moderator role is required. Captains can report only their own team’s loss.',
        );
      if (matchOps.includes(request.op)) {
        const m = current.selected;
        if (!request.matchId || !m || m.version !== request.version)
          throw new PlayError('This match changed. Refresh the admin panel.');
        if (!['check_in', 'playing'].includes(m.state))
          throw new PlayError('This match has ended. Refresh the admin panel.');
        if (['alpha_loss', 'beta_loss', 'draw'].includes(request.op) && m.state !== 'playing')
          throw new PlayError('Wait for check-in to finish before recording a result.');
      } else if (request.version !== current.queueVersion)
        throw new PlayError('This queue changed. Refresh the admin panel.');
    };
    validate();
    if (confirmedAdminOps.has(request.op) && !request.confirm)
      return { view, confirmation: request.op };
    const kind =
      !view.canModerate && ['alpha_loss', 'beta_loss'].includes(request.op)
        ? 'loss'
        : !view.canModerate && request.op === 'recover'
          ? 'teams'
          : (
              {
                start: 'start',
                clear: 'clear',
                queue: 'queue',
                alpha_loss: 'admin_report',
                beta_loss: 'admin_report',
                draw: 'admin_report',
                cancel: 'recover_abort',
                recover: 'recover',
                close_betting: 'bet_close',
              } as const
            )[request.op as Exclude<AdminRequest['op'], 'view' | 'match'>];
    const reply = await this.act(
      guild,
      {
        id: request.id,
        actor: request.actor,
        channelId: request.channelId,
        admin: view.canModerate,
        kind,
        queue: view.queue.id,
        ...(matchOps.includes(request.op)
          ? { matchId: request.matchId, version: request.version }
          : {}),
        ...(['alpha_loss', 'beta_loss', 'draw'].includes(request.op)
          ? {
              result:
                request.op === 'alpha_loss'
                  ? ('beta' as const)
                  : request.op === 'beta_loss'
                    ? ('alpha' as const)
                    : ('draw' as const),
            }
          : {}),
      },
      validate,
    );
    if (reply.ok === false) throw new PlayError(reply.content);
    return {
      view: this.adminView(guild, {
        ...request,
        queue: view.queue.id,
        matchId: undefined,
        op: 'view',
      }),
    };
  }
  async act(guild: string, input: Action, validate?: () => void): Promise<Reply> {
    if (this.ctx.storage.kv.get('recoveryReadOnly'))
      return {
        ok: false,
        content: 'This restored community is read-only for recovery inspection.',
      };
    const a = actionSchema.parse(input);
    const before = this.read(guild);
    let candidateQueues: Queue[] = [];
    if (['start', 'split', 'subauto', 'back'].includes(a.kind))
      candidateQueues = before.settings.queues.filter((q) => q.channelId === a.channelId);
    if (a.kind === 'add')
      candidateQueues = before.settings.queues.filter(
        (q) => q.channelId === a.channelId && this.entries(q.id).length >= q.size - 1,
      );
    if (['subauto', 'back'].includes(a.kind)) {
      const outgoing = a.target ?? a.actor;
      const active = this.sql
        .exec<{ match_id: string }>(
          'SELECT match_id FROM active_players WHERE user_id=?',
          outgoing.id,
        )
        .toArray()[0];
      if (a.matchId || active) {
        const row = this.sql
          .exec<MatchRow>('SELECT config FROM matches WHERE id=?', a.matchId ?? active!.match_id)
          .toArray()[0];
        // Invalid IDs are rejected by the transactional command handler, not the role preflight.
        if (row) candidateQueues.push((JSON.parse(row.config) as Match).queue);
      }
    }
    const overdue = this.sql
      .exec<{ match_id: string }>(
        "SELECT match_id FROM deadlines WHERE kind='check_in' AND due<=? LIMIT 32",
        Date.now(),
      )
      .toArray();
    candidateQueues.push(...overdue.map((row) => this.match(row.match_id).queue));
    if (a.kind === 'split' && !a.admin) candidateQueues = [];
    // Roles are refreshed only at restricted roster selection, never by an idle loop.
    await this.refreshCandidates(guild, candidateQueues);
    const result = await this.ctx.storage.transaction(async () => {
      const snapshot = this.read(guild),
        now = Date.now();
      this.expire(now, snapshot.settings);
      const targetId =
        a.matchId ??
        this.sql
          .exec<{ match_id: string }>(
            'SELECT match_id FROM active_players WHERE user_id=?',
            a.actor.id,
          )
          .toArray()[0]?.match_id;
      const targetRow = targetId
        ? this.sql.exec<MatchRow>('SELECT config FROM matches WHERE id=?', targetId).toArray()[0]
        : undefined;
      if (targetRow) {
        const target = JSON.parse(targetRow.config) as Match;
        if (target.expiresAt !== undefined && now >= target.expiresAt)
          this.timeoutMatch(target, now);
      }
      const fingerprint = JSON.stringify(a);
      const previous = this.sql
        .exec<{ fingerprint: string; reply: string }>(
          'SELECT fingerprint,reply FROM play_actions WHERE id=?',
          a.id,
        )
        .toArray()[0];
      if (previous) {
        await this.schedule();
        return previous.fingerprint === fingerprint
          ? JSON.parse(previous.reply)
          : { content: 'This interaction was already processed with different input.', ok: false };
      }
      let reply: Reply;
      // A rejected action rolls back its own partial changes, while expired deadlines remain committed.
      try {
        reply = this.ctx.storage.transactionSync(() => {
          validate?.();
          return this.dispatch(a, snapshot.settings);
        });
      } catch (e) {
        if (!(e instanceof PlayError)) throw e;
        reply = { content: e.message, ok: false };
      }
      if (reply.ok !== false) {
        let key =
          a.matchId && !['add', 'remove', 'queue'].includes(a.kind)
            ? `${a.kind.startsWith('civ_') ? 'civ' : 'match'}:${a.matchId}`
            : undefined;
        if (['add', 'start'].includes(a.kind)) {
          const active = this.sql
            .exec<{ match_id: string }>(
              'SELECT match_id FROM active_players WHERE user_id=?',
              a.target?.id ?? a.actor.id,
            )
            .toArray()[0];
          if (active) key = `match:${active.match_id}`;
        }
        if (!key && ['add', 'remove', 'queue'].includes(a.kind))
          key = `queue:${this.queue(a, snapshot.settings).id}`;
        if (key) {
          const card = this.sql
            .exec<Pointer>('SELECT * FROM cards WHERE key=? AND channel_id=?', key, a.channelId)
            .toArray()[0];
          if (
            card?.message_id &&
            (key.startsWith('match:') ||
              !this.sql
                .exec(
                  "SELECT 1 FROM outbox WHERE key IN (SELECT key FROM queue_publications WHERE queue_id=?) AND status='pending' LIMIT 1",
                  key.slice(6),
                )
                .toArray().length)
          )
            reply.link = {
              label: key.startsWith('civ:')
                ? 'View civilization picks'
                : key.startsWith('match:')
                  ? 'View match'
                  : 'View queue',
              url: `https://discord.com/channels/${guild}/${a.channelId}/${card.message_id}`,
            };
        }
      }
      this.sql.exec(
        'INSERT INTO play_actions VALUES(?,?,?,?)',
        a.id,
        fingerprint,
        JSON.stringify(reply),
        now,
      );
      this.sql.exec('DELETE FROM play_actions WHERE created_at<?', now - 7 * 86400000);
      await this.schedule();
      return reply;
    });
    await this.schedule();
    return result;
  }
  assertSettings(old: Settings, next: Settings) {
    for (const game of old.games) {
      const engine =
        this.sql
          .exec<{ engine: string }>('SELECT engine FROM rating_engines WHERE game_id=?', game.id)
          .toArray()[0]?.engine ?? old.queues.find((q) => q.gameId === game.id)?.ratingEngine;
      const after = next.queues.find((q) => q.gameId === game.id);
      if (
        engine &&
        after &&
        engine !== after.ratingEngine &&
        this.sql.exec('SELECT 1 FROM profiles WHERE game_id=? LIMIT 1', game.id).toArray().length
      )
        throw new PlayError(
          'Changing the rating engine for an established game requires an explicit conversion; keep the current engine.',
        );
    }
    for (const game of old.games)
      if (
        !next.games.some((g) => g.id === game.id) &&
        (this.sql.exec('SELECT 1 FROM matches WHERE game_id=? LIMIT 1', game.id).toArray().length ||
          this.sql.exec('SELECT 1 FROM profiles WHERE game_id=? LIMIT 1', game.id).toArray()
            .length ||
          this.sql.exec('SELECT 1 FROM wallets WHERE game_id=? LIMIT 1', game.id).toArray().length)
      )
        throw new PlayError(
          'A game with player records or matches cannot be removed. Rename it instead.',
        );
    for (const q of old.queues) {
      const after = next.queues.find((n) => n.id === q.id);
      const busy =
        this.entries(q.id).length ||
        this.sql
          .exec(
            "SELECT 1 FROM matches WHERE queue_id=? AND state IN ('check_in','playing') LIMIT 1",
            q.id,
          )
          .toArray().length;
      if (busy && (!after || after.gameId !== q.gameId || after.channelId !== q.channelId))
        throw new PlayError(
          'Empty the waiting queue and finish its active matches before removing or moving it.',
        );
    }
    for (const q of next.queues) {
      const peers = next.queues.filter((p) => p.gameId === q.gameId);
      if (
        peers.some(
          (p) =>
            p.ratingEngine !== q.ratingEngine ||
            JSON.stringify(policy(p)) !== JSON.stringify(policy(q)),
        )
      )
        throw new PlayError(
          'Queues for the same game share ratings and must use the same rating engine and settings.',
        );
    }
  }
  async alarm(guild: string) {
    const settings = this.read(guild).settings;
    const due = this.sql
      .exec<{ match_id: string }>(
        "SELECT match_id FROM deadlines WHERE kind='check_in' AND due<=? LIMIT 32",
        Date.now(),
      )
      .toArray();
    await this.refreshCandidates(guild, [
      ...settings.queues.filter((q) =>
        due.some((row) => this.match(row.match_id).queue.id === q.id),
      ),
      ...due.map((row) => this.match(row.match_id).queue),
    ]);
    await this.ctx.storage.transaction(async () => {
      this.expire(Date.now(), this.read(guild).settings);
      this.recordStore.decay(guild);
      this.economy.tax();
      await this.schedule();
    });
    await this.lobbies.poll(guild);
    await this.returnPlayers(guild);
    await this.refreshQueueVoice(guild);
    await this.refreshQueueVisibility(guild);
    await this.schedule();
    await this.flush();
  }
  async schedule() {
    if (this.ctx.storage.kv.get('recoveryReadOnly')) {
      await this.ctx.storage.deleteAlarm();
      return;
    }
    const values = [
      ['deadlines', ''],
      ['queue_clocks', ''],
      ['outbox', "WHERE status='pending'"],
      ['pending_returns', "WHERE status='pending'"],
      ['record_clocks', ''],
      ['rank_jobs', "WHERE status='pending'"],
      ['economy_clocks', ''],
    ]
      .map(
        ([table, filter]) =>
          this.sql
            .exec<{ due: number | null }>(`SELECT MIN(due) AS due FROM ${table} ${filter}`)
            .toArray()[0]?.due,
      )
      .filter((due): due is number => due != null);
    const identity = this.sql
      .exec<{ guild_id: string }>('SELECT guild_id FROM community WHERE id=1')
      .toArray()[0];
    if (identity && this.voiceQueues(this.read(identity.guild_id).settings).length) {
      let cache = this.ctx.storage.kv.get<QueueVoiceCache>('queueVoice');
      if (!cache) {
        cache = { due: Date.now(), observedAt: 0, members: null };
        this.ctx.storage.kv.put('queueVoice', cache);
      }
      values.push(cache.due);
    } else this.ctx.storage.kv.delete('queueVoice');
    if (identity && this.visibilityQueues(this.read(identity.guild_id).settings).length) {
      let due = this.ctx.storage.kv.get<number>('queueVisibilityDue');
      if (due === undefined) {
        due = Date.now() + queueVisibilityInterval;
        this.ctx.storage.kv.put('queueVisibilityDue', due);
      }
      values.push(due);
    } else this.ctx.storage.kv.delete('queueVisibilityDue');
    const next = values.length ? Math.min(...values) : null;
    if (next !== null && next !== undefined)
      await this.ctx.storage.setAlarm(Math.max(Date.now() + 50, next));
    else await this.ctx.storage.deleteAlarm();
  }
  records(guild: string, gameId?: string, playersPage = 0) {
    const snapshot = this.read(guild);
    if (gameId && !snapshot.settings.games.some((g) => g.id === gameId))
      throw new PlayError('Game not found in this community.');
    if (!Number.isInteger(playersPage) || playersPage < 0 || playersPage > 2000)
      throw new PlayError('Choose a valid player page.');
    const playersTotal = this.sql
      .exec<{ count: number }>(
        `SELECT COUNT(*) AS count FROM profiles ${gameId ? 'WHERE game_id=?' : ''}`,
        ...(gameId ? [gameId] : []),
      )
      .toArray()[0].count;
    const matches = this.sql
      .exec<{ config: string }>(
        `SELECT config FROM matches ${gameId ? 'WHERE game_id=?' : ''} ORDER BY created_at DESC LIMIT 20`,
        ...(gameId ? [gameId] : []),
      )
      .toArray()
      .map((row) => JSON.parse(row.config) as Match);
    const players = this.sql
      .exec<{ game_id: string; config: string }>(
        `SELECT game_id,config FROM profiles ${gameId ? 'WHERE game_id=?' : ''} ORDER BY json_extract(config,'$.rating') DESC,game_id,user_id LIMIT 50 OFFSET ?`,
        ...(gameId ? [gameId] : []),
        playersPage * 50,
      )
      .toArray()
      .map((row) => ({ gameId: row.game_id, ...(JSON.parse(row.config) as Player) }));
    const changes = this.sql
      .exec(
        `SELECT * FROM rating_changes ${gameId ? 'WHERE game_id=?' : ''} ORDER BY changed_at DESC LIMIT 50`,
        ...(gameId ? [gameId] : []),
      )
      .toArray();
    const delivery = this.sql
      .exec('SELECT key,status,attempts,error FROM outbox ORDER BY due LIMIT 20')
      .toArray();
    const audit = this.sql
      .exec(
        'SELECT id,match_id,queue_id,action,actor_id,at,details FROM control_audit ORDER BY at DESC LIMIT 50',
      )
      .toArray();
    const waiting = snapshot.settings.queues.map((q) => ({
      queueId: q.id,
      channelId: q.channelId,
      players: this.entries(q.id).map((e) => ({ id: e.user_id, name: e.display_name })),
    }));
    const noadds = this.sql
      .exec('SELECT user_id,until_ms,reason,actor_id FROM noadds WHERE until_ms>?', Date.now())
      .toArray();
    return {
      matches,
      players,
      playersTotal,
      playersPage,
      changes,
      delivery,
      audit,
      waiting,
      noadds,
      onboarding: this.recordStore.diagnostics(guild),
      economy: this.economy.records(gameId),
      pendingReturns: this.sql
        .exec('SELECT * FROM pending_returns ORDER BY seq LIMIT 100')
        .toArray(),
    };
  }
  flush() {
    if (this.ctx.storage.kv.get('recoveryReadOnly')) return Promise.resolve();
    if (!this.flushing)
      this.flushing = this.deliver().finally(() => {
        this.flushing = null;
      });
    return this.flushing;
  }
  private async deliver() {
    const identity = this.sql
      .exec<{ guild_id: string }>('SELECT guild_id FROM community WHERE id=1')
      .toArray()[0];
    if (identity) await this.recordStore.ranks(identity.guild_id);
    for (let batch = 0; batch < 8; batch++) {
      const work = this.sql
        .exec<Work>(
          "SELECT * FROM outbox WHERE status='pending' AND due<=? ORDER BY due,key LIMIT 1",
          Date.now(),
        )
        .toArray()[0];
      if (!work) break;
      if (!this.transientIsCurrent(work.key)) {
        this.retireTransient(work.key);
        continue;
      }
      if (work.key.startsWith('end:')) {
        const [, id, version] = work.key.split(':'),
          match = this.match(id);
        if (
          match.state !== 'playing' ||
          String(match.version) !== version ||
          (match.expiresAt !== undefined && Date.now() >= match.expiresAt)
        ) {
          this.sql.exec('DELETE FROM outbox WHERE key=?', work.key);
          continue;
        }
      }
      if (work.key.startsWith('rally:')) {
        const rally = this.sql
          .exec<{ queue_id: string; created_at: number; targets: string }>(
            'SELECT * FROM rally_runs WHERE id=?',
            work.key.slice(6),
          )
          .toArray()[0];
        const q = identity
          ? this.read(identity.guild_id).settings.queues.find((q) => q.id === rally?.queue_id)
          : undefined;
        if (
          !rally ||
          !q?.enabled ||
          !q.rally?.enabled ||
          Date.now() - rally.created_at > 30000 ||
          this.entries(q.id).length >= q.size
        ) {
          this.sql.exec('DELETE FROM outbox WHERE key=?', work.key);
          continue;
        }
        const state = await this.env.VOICE_PRESENCE.getByName(
          this.env.DISCORD_APPLICATION_ID!,
        ).query(identity!.guild_id);
        const saved = JSON.parse(rally.targets) as { channel: string; users: string[] };
        const targets = saved.users;
        if (
          !state.members ||
          targets.some(
            (id) =>
              !state.members!.some((p) => p.userId === id && p.channelId === saved.channel) ||
              !this.allowed(q, id, true) ||
              this.entries(q.id).some((e) => e.user_id === id) ||
              this.sql.exec('SELECT 1 FROM active_players WHERE user_id=?', id).toArray().length ||
              this.sql.exec('SELECT 1 FROM pending_returns WHERE user_id=?', id).toArray().length,
          )
        ) {
          this.sql.exec('DELETE FROM outbox WHERE key=?', work.key);
          continue;
        }
      }
      const publication = this.sql
        .exec<{
          queue_id: string;
          generation: number;
          state: string;
          visibility_only: number;
          after_matches: string;
        }>('SELECT * FROM queue_publications WHERE key=?', work.key)
        .toArray()[0];
      const card = this.sql.exec<Pointer>('SELECT * FROM cards WHERE key=?', work.key).toArray()[0];
      if (publication) {
        const current = this.sql
          .exec<{ current_generation: number; message_id: string | null }>(
            'SELECT v.current_generation,c.message_id FROM queue_views v LEFT JOIN cards c ON c.key=? WHERE v.queue_id=?',
            `queue:${publication.queue_id}`,
            publication.queue_id,
          )
          .toArray()[0];
        if (
          publication.state === 'superseded' ||
          (publication.generation < (current?.current_generation ?? 0) &&
            ((!card.message_id && !card.reconcile) ||
              (card.message_id && card.message_id === current?.message_id)))
        ) {
          // An older retry may share the current message after an in-place edit.
          // Discard its work without editing or deleting that shared message.
          this.sql.exec('DELETE FROM outbox WHERE key=? AND version=?', work.key, work.version);
          this.sql.exec("UPDATE queue_publications SET state='superseded' WHERE key=?", work.key);
          continue;
        }
        const waitingFor = this.sql
          .exec<{ status: string | null; due: number | null }>(
            "SELECT o.status,o.due FROM json_each(?) d LEFT JOIN cards c ON c.key='match:'||d.value LEFT JOIN outbox o ON o.key='match:'||d.value WHERE c.message_id IS NULL",
            publication.after_matches,
          )
          .toArray();
        if (waitingFor.length) {
          // Persist the ordering across rate limits, lost receipts and restarts.
          // Block with the failed match delivery rather than polling indefinitely.
          const blocked = waitingFor.some((w) => w.status !== 'pending');
          this.sql.exec(
            "UPDATE outbox SET status=?,error='waiting_for_match_card',due=? WHERE key=? AND version=?",
            blocked ? 'blocked' : 'pending',
            Math.max(
              Date.now() + 1000,
              Math.min(...waitingFor.map((w) => w.due ?? Date.now())) + 1,
            ),
            work.key,
            work.version,
          );
          continue;
        }
      }
      const gate = this.env.DISCORD_GATE.getByName(this.env.DISCORD_APPLICATION_ID!);
      const route = `channel:${card.channel_id}`;
      const at = await gate.reserve(route);
      if (at > Date.now()) {
        this.sql.exec(
          'UPDATE outbox SET due=? WHERE key=? AND version=?',
          at,
          work.key,
          work.version,
        );
        continue;
      }
      if (
        this.sql
          .exec<{ version: number }>('SELECT version FROM outbox WHERE key=?', work.key)
          .toArray()[0]?.version !== work.version
      )
        continue;
      if (!this.transientIsCurrent(work.key)) {
        this.retireTransient(work.key);
        continue;
      }
      if (publication)
        this.sql.exec("UPDATE queue_publications SET state='sending' WHERE key=?", work.key);
      try {
        if (!this.env.DISCORD_BOT_TOKEN) throw new Error('No bot token.');
        const headers = {
          Authorization: `Bot ${this.env.DISCORD_BOT_TOKEN}`,
          'Content-Type': 'application/json',
        };
        if (
          publication &&
          publication.after_matches === '[]' &&
          !card.message_id &&
          !card.reconcile
        ) {
          const previous = this.sql
            .exec<Pointer>('SELECT * FROM cards WHERE key=?', `queue:${publication.queue_id}`)
            .toArray()[0];
          if (previous?.message_id) {
            // Check public channel history only when an actual queue update is due.
            // Keep a recent card in place; publish and retire it only when it fell below the last five.
            let latest: { id: string }[];
            try {
              const response = await fetch(
                `https://discord.com/api/v10/channels/${card.channel_id}/messages?limit=5`,
                { headers, signal: AbortSignal.timeout(5000) },
              );
              if (!response.ok) {
                if (response.status >= 500) await this.retry(work, 'queue_history_unavailable');
                else await this.failed(work, route, response, gate);
                continue;
              }
              await this.rateHeaders(response, route, gate);
              const rows: unknown = await response.json();
              if (!Array.isArray(rows) || rows.some((r) => !r || !/^\d{17,20}$/.test(r.id)))
                throw new Error('Invalid channel history');
              latest = rows.slice(0, 5);
            } catch {
              await this.retry(work, 'queue_history_unavailable');
              continue;
            }
            if (latest.some((r) => r.id === previous.message_id)) {
              card.message_id = previous.message_id;
              this.sql.exec('UPDATE cards SET message_id=? WHERE key=?', card.message_id, card.key);
            }
            // History and the subsequent edit/create both count against Discord's limits.
            const next = await gate.reserve(route);
            if (next > Date.now()) {
              this.sql.exec(
                'UPDATE outbox SET due=? WHERE key=? AND version=?',
                next,
                work.key,
                work.version,
              );
              continue;
            }
          }
        }
        if (!card.message_id && card.reconcile) {
          const check = await fetch(
            `https://discord.com/api/v10/channels/${card.channel_id}/messages?limit=100${card.reconcile_cursor ? `&before=${card.reconcile_cursor}` : ''}`,
            { headers, signal: AbortSignal.timeout(5000) },
          );
          if (!check.ok) {
            await this.failed(work, route, check, gate);
            continue;
          }
          await this.rateHeaders(check, route, gate);
          const rows = (await check.json()) as {
            id: string;
            author: { id: string };
            nonce?: string | number;
            embeds?: { footer?: { text?: string } }[];
            components?: { components?: { custom_id?: string }[] }[];
          }[];
          const existing = rows.find(
            (r) =>
              r.author.id === this.env.DISCORD_APPLICATION_ID &&
              (String(r.nonce ?? '') === card.nonce ||
                r.embeds?.some((e) => e.footer?.text === `Community Games · ${work.key}`) ||
                ((work.key.startsWith('queue:') || work.key.startsWith('match:')) &&
                  // Match cards have embeds; a reminder must not be mistaken for the match itself.
                  !!r.embeds?.length &&
                  r.components?.some((row) =>
                    row.components?.some(
                      (c) =>
                        c.custom_id?.startsWith(
                          `${work.key.startsWith('queue:') ? 'q' : 'm'}:${work.key.split(':')[1]}:`,
                        ) &&
                        (!publication || c.custom_id.endsWith(`:g${publication.generation}`)),
                    ),
                  ))),
          );
          if (!existing && publication && rows.length === 100 && (card.reconcile_pages ?? 0) < 20) {
            const last = rows[rows.length - 1]?.id;
            if (last && /^\d{17,20}$/.test(last) && last !== card.reconcile_cursor) {
              this.sql.exec(
                'UPDATE cards SET reconcile_cursor=?,reconcile_pages=reconcile_pages+1 WHERE key=?',
                last,
                card.key,
              );
              this.sql.exec(
                'UPDATE outbox SET due=? WHERE key=? AND version=?',
                Date.now() + 100,
                work.key,
                work.version,
              );
              continue;
            }
          }
          if (existing) {
            card.message_id = existing.id;
            this.sql.exec(
              'UPDATE cards SET message_id=?,reconcile=0,announced=1,reconcile_cursor=NULL,reconcile_pages=0 WHERE key=?',
              existing.id,
              card.key,
            );
          }
          this.sql.exec('UPDATE cards SET reconcile=0 WHERE key=?', card.key);
          if (
            publication &&
            !existing &&
            (work.attempts >= 2 || (card.reconcile_pages ?? 0) >= 20)
          ) {
            this.sql.exec(
              "UPDATE outbox SET status='blocked',error='ambiguous_publication' WHERE key=? AND version=?",
              work.key,
              work.version,
            );
            this.sql.exec("UPDATE queue_publications SET state='uncertain' WHERE key=?", work.key);
            continue;
          }
          // Reserve again before a second HTTP call; global limits cover reconciliation too.
          const next = await gate.reserve(route);
          if (next > Date.now()) {
            this.sql.exec(
              'UPDATE outbox SET due=? WHERE key=? AND version=?',
              next,
              work.key,
              work.version,
            );
            continue;
          }
        }
        let payload = JSON.parse(work.payload);
        if (
          publication?.visibility_only &&
          identity &&
          this.sql
            .exec<{ visibility_only: number }>(
              'SELECT visibility_only FROM queue_publications WHERE key=?',
              work.key,
            )
            .toArray()[0]?.visibility_only
        ) {
          const q = this.read(identity.guild_id).settings.queues.find(
            (q) => q.id === publication.queue_id,
          );
          if (!q?.enabled || q.keepQueueVisible === false || q.channelId !== card.channel_id) {
            // A settings change cancels a queued bump. Reconcile a lost create first,
            // then remove only that orphan, never the current public card.
            const current = this.sql
              .exec<Pointer>('SELECT * FROM cards WHERE key=?', `queue:${publication.queue_id}`)
              .toArray()[0];
            if (card.message_id && card.message_id !== current?.message_id)
              this.retireMessage(work.key, card.channel_id, card.message_id);
            this.sql.exec('DELETE FROM outbox WHERE key=? AND version=?', work.key, work.version);
            this.sql.exec("UPDATE queue_publications SET state='superseded' WHERE key=?", work.key);
            continue;
          }
        }
        if (work.key.startsWith('ping:')) {
          // A recovered receipt means the invitation already exists. Never edit
          // it or repeat its mentions after a lost HTTP response.
          if (card.message_id) {
            this.sql.exec('DELETE FROM outbox WHERE key=? AND version=?', work.key, work.version);
            continue;
          }
          payload = identity ? await this.freshPing(work, identity.guild_id) : null;
          if (!payload) {
            this.sql.exec('DELETE FROM outbox WHERE key=? AND version=?', work.key, work.version);
            continue;
          }
        }
        if (!card.message_id) {
          payload.nonce = card.nonce;
          payload.enforce_nonce = true;
          if (work.key.startsWith('match:') && !card.announced) {
            const m = this.match(work.key.slice(6));
            if (
              ['check_in', 'playing'].includes(m.state) &&
              (!m.deadline || m.deadline > Date.now())
            ) {
              const maps = m.mapRound?.state === 'open' ? m.mapRound : undefined;
              const invitees =
                m.state === 'check_in'
                  ? m.roster.filter((p) =>
                      maps?.settings.mode === 'direct'
                        ? m.captains.includes(p.id)
                        : maps
                          ? !maps.ballots[p.id] ||
                            (m.queue.checkIn.enabled && !m.ready.includes(p.id))
                          : !m.ready.includes(p.id),
                    )
                  : m.roster;
              const instruction = maps
                ? maps.settings.mode === 'direct'
                  ? 'Captains, choose your maps below.'
                  : maps.settings.countsAsCheckIn && m.queue.checkIn.enabled
                    ? 'Vote for your maps to check in.'
                    : m.queue.checkIn.enabled && m.ready.length < m.roster.length
                      ? 'Check in and choose your maps below.'
                      : 'Choose your maps below.'
                : m.state === 'check_in'
                  ? 'Your match is ready. Check in below.'
                  : 'Your teams are ready.';
              payload.content = [invitees.map((p) => `<@${p.id}>`).join(' '), instruction]
                .filter(Boolean)
                .join('\n');
              payload.allowed_mentions = { parse: [], users: invitees.map((p) => p.id) };
            }
          }
        }
        const response = await fetch(
          `https://discord.com/api/v10/channels/${card.channel_id}/messages${card.message_id ? `/${card.message_id}` : ''}`,
          {
            method: work.key.startsWith('retire:') ? 'DELETE' : card.message_id ? 'PATCH' : 'POST',
            headers,
            body: work.key.startsWith('retire:') ? undefined : JSON.stringify(payload),
            signal: AbortSignal.timeout(5000),
          },
        );
        if (response.ok) {
          await this.rateHeaders(response, route, gate);
          if (work.key.startsWith('retire:')) {
            this.sql.exec('DELETE FROM outbox WHERE key=? AND version=?', work.key, work.version);
            this.sql.exec('DELETE FROM cards WHERE key=?', work.key);
            continue;
          }
          const message = (await response.json()) as { id: string };
          if (!/^\d{17,20}$/.test(message.id)) throw new Error('Invalid message receipt.');
          // A round may end while Discord accepts its HTTP request. Remove that
          // late receipt rather than leave an expired countdown or agreement.
          const transient = /^(reminder|agreement|end):/.test(work.key);
          const currentPointer = transient
            ? this.sql.exec<Pointer>('SELECT * FROM cards WHERE key=?', work.key).toArray()[0]
            : undefined;
          if (
            !this.transientIsCurrent(work.key) ||
            (transient && currentPointer?.nonce !== card.nonce)
          ) {
            if (currentPointer?.nonce === card.nonce) this.retireTransient(work.key);
            this.retireMessage(work.key, card.channel_id, message.id);
            continue;
          }
          this.ctx.storage.transactionSync(() => {
            this.sql.exec(
              'UPDATE cards SET message_id=?,reconcile=0,announced=1,reconcile_cursor=NULL,reconcile_pages=0 WHERE key=?',
              message.id,
              card.key,
            );
            this.sql.exec('DELETE FROM outbox WHERE key=? AND version=?', work.key, work.version);
            if (publication) {
              this.sql.exec("UPDATE queue_publications SET state='sent' WHERE key=?", work.key);
              const view = this.sql
                .exec<{ current_generation: number }>(
                  'SELECT current_generation FROM queue_views WHERE queue_id=?',
                  publication.queue_id,
                )
                .toArray()[0];
              const logical = `queue:${publication.queue_id}`;
              const old = this.sql
                .exec<Pointer>('SELECT * FROM cards WHERE key=?', logical)
                .toArray()[0];
              if (publication.generation > (view?.current_generation ?? 0)) {
                this.sql.exec(
                  'UPDATE cards SET message_id=?,announced=1 WHERE key=?',
                  message.id,
                  logical,
                );
                this.sql.exec(
                  'UPDATE queue_views SET current_generation=? WHERE queue_id=?',
                  publication.generation,
                  publication.queue_id,
                );
                if (old?.message_id && old.message_id !== message.id)
                  this.retireMessage(logical, card.channel_id, old.message_id);
              } else if (old?.message_id !== message.id)
                this.retireMessage(work.key, card.channel_id, message.id);
            }
            if (work.key.startsWith('match:'))
              this.sql.exec(
                "UPDATE outbox SET status='pending',error=NULL,due=? WHERE error='waiting_for_match_card' AND key IN (SELECT p.key FROM queue_publications p,json_each(p.after_matches) d WHERE d.value=?)",
                Date.now(),
                work.key.slice(6),
              );
          });
        } else if (response.status === 404 && work.key.startsWith('retire:')) {
          this.sql.exec('DELETE FROM outbox WHERE key=? AND version=?', work.key, work.version);
        } else if (response.status === 404 && card.message_id) {
          // Recreate a deleted card with a new nonce. Domain state and old-button checks remain authoritative.
          this.sql.exec(
            'UPDATE cards SET message_id=NULL,nonce=?,reconcile=0 WHERE key=?',
            crypto.randomUUID().replaceAll('-', '').slice(0, 24),
            card.key,
          );
          await this.retry(work, 'message_deleted', 250);
        } else await this.failed(work, route, response, gate);
      } catch {
        if (!card.message_id) this.sql.exec('UPDATE cards SET reconcile=1 WHERE key=?', card.key);
        await this.retry(work, 'transport_failed');
      }
    }
    await this.ctx.storage.transaction(async () => {
      await this.schedule();
    });
  }
  private async rateHeaders(
    response: Response,
    route: string,
    gate: DurableObjectStub<import('./discord-gate').DiscordGate>,
  ) {
    const reset = Number(response.headers.get('X-RateLimit-Reset-After'));
    if (response.headers.get('X-RateLimit-Remaining') === '0' && Number.isFinite(reset))
      await gate.pause(route, Date.now() + Math.max(100, reset * 1000));
  }
  private async failed(
    work: Work,
    route: string,
    response: Response,
    gate: DurableObjectStub<import('./discord-gate').DiscordGate>,
  ) {
    if (response.status === 429) {
      const body = (await response.json().catch(() => null)) as {
        retry_after?: number;
        global?: boolean;
      } | null;
      const seconds = Number(body?.retry_after ?? response.headers.get('Retry-After') ?? 1);
      const wait = Number.isFinite(seconds) ? Math.max(1000, seconds * 1000) : 1000;
      await gate.pause(route, Date.now() + wait, body?.global === true);
      await this.retry(work, 'rate_limited', wait);
    } else if ([400, 401, 403, 404].includes(response.status))
      this.sql.exec(
        "UPDATE outbox SET status='blocked',error=? WHERE key=? AND version=?",
        `discord_${response.status}`,
        work.key,
        work.version,
      );
    else {
      if (
        !this.sql.exec<Pointer>('SELECT * FROM cards WHERE key=?', work.key).toArray()[0].message_id
      )
        this.sql.exec('UPDATE cards SET reconcile=1 WHERE key=?', work.key);
      await this.retry(work, `discord_${response.status}`);
    }
  }
  private async retry(work: Work, error: string, delay?: number) {
    const attempts = work.attempts + 1;
    // Finite retries prevent an outage from becoming an endless paid polling loop.
    this.sql.exec(
      'UPDATE outbox SET attempts=?,error=?,status=?,due=? WHERE key=? AND version=?',
      attempts,
      error,
      attempts >= 10 ? 'blocked' : 'pending',
      Date.now() + (delay ?? Math.min(900000, 1000 * 2 ** attempts)),
      work.key,
      work.version,
    );
  }
}

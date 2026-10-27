import { gameAdapter } from '../games/registry';
import { recordActionSchema } from '../domain/records';
import type { LobbyEvent } from '../domain/lobby';
import { DurableObject } from 'cloudflare:workers';
import type { Env } from '../env';
import { settingsSchema, normalizeDefaults, type Settings, type Snapshot } from '../domain/config';
import { createPlayTables, PlayStore } from './play-store';
import { PlayError, type Action } from '../domain/play';
import { createRecordTables, RecordStore } from './record-store';
import type { ImportBundle, RecordAction } from '../domain/records';
import { EconomyStore } from './economy-store';
import type { EconomyImport } from '../domain/economy-import';

export class Community extends DurableObject<Env> {
  private playStore: PlayStore;
  private recordStore: RecordStore;
  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    ctx.storage.transactionSync(() => {
      const sql = ctx.storage.sql;
      sql.exec('CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)');
      const version = sql
        .exec<{ version: number }>('SELECT version FROM schema_version')
        .toArray()[0]?.version;
      if (version && ![1, 2, 3, 4, 5, 6, 7].includes(version))
        throw new Error('Unsupported storage schema.');
      if (!version) sql.exec('INSERT INTO schema_version (version) VALUES (1)');
      sql.exec(
        `CREATE TABLE IF NOT EXISTS community (id INTEGER PRIMARY KEY CHECK (id=1), guild_id TEXT NOT NULL, revision INTEGER NOT NULL)`,
      );
      sql.exec(`CREATE TABLE IF NOT EXISTS games (id TEXT PRIMARY KEY, config TEXT NOT NULL)`);
      sql.exec(
        `CREATE TABLE IF NOT EXISTS queues (id TEXT PRIMARY KEY, game_id TEXT NOT NULL REFERENCES games(id), config TEXT NOT NULL)`,
      );
      sql.exec(`CREATE INDEX IF NOT EXISTS queue_game ON queues(game_id)`);
      sql.exec(
        `CREATE TABLE IF NOT EXISTS settings_audit (revision INTEGER PRIMARY KEY, actor_id TEXT NOT NULL, action TEXT NOT NULL, changed_at TEXT NOT NULL)`,
      );
      createPlayTables(sql);
      createRecordTables(sql);
      const rows = sql
        .exec<{ id: string; config: string }>('SELECT id,config FROM queues ORDER BY id')
        .toArray();
      const queues = rows.map((r) => JSON.parse(r.config) as Settings['queues'][number]);
      for (const channel of new Set(queues.map((q) => q.channelId))) {
        const group = queues.filter((q) => q.channelId === channel);
        const selected = group.find((q) => q.isDefault) ?? group[0];
        for (const q of group)
          if (q.isDefault !== (q.id === selected.id)) {
            q.isDefault = q.id === selected.id;
            sql.exec('UPDATE queues SET config=? WHERE id=?', JSON.stringify(q), q.id);
          }
      }
      sql.exec('UPDATE schema_version SET version=7');
    });
    this.recordStore = new RecordStore(ctx, env, (guild) => this.read(guild));
    this.recordStore.backfill();
    this.playStore = new PlayStore(ctx, env, (guild) => this.read(guild), this.recordStore);
  }

  private identity(guildId: string) {
    const sql = this.ctx.storage.sql;
    let row = sql
      .exec<{ guild_id: string; revision: number }>(
        'SELECT guild_id, revision FROM community WHERE id=1',
      )
      .toArray()[0];
    if (!row) {
      sql.exec('INSERT INTO community VALUES (1, ?, 0)', guildId);
      row = { guild_id: guildId, revision: 0 };
    }
    if (row.guild_id !== guildId) throw new Error('Community identity mismatch.');
    return row;
  }

  read(guildId: string): Snapshot {
    const { revision } = this.identity(guildId);
    const sql = this.ctx.storage.sql;
    return {
      communityId: guildId,
      revision,
      settings: {
        games: sql
          .exec<{ config: string }>('SELECT config FROM games ORDER BY id')
          .toArray()
          .map((r) => JSON.parse(r.config)),
        queues: sql
          .exec<{ config: string }>('SELECT config FROM queues ORDER BY id')
          .toArray()
          .map((r) => JSON.parse(r.config)),
      },
    };
  }

  async save(
    guildId: string,
    expectedRevision: number,
    settings: Settings,
    actorId: string,
    action: 'save',
  ) {
    if (this.ctx.storage.kv.get('recoveryReadOnly'))
      return {
        ok: false as const,
        revision: this.identity(guildId).revision,
        reason: 'Recovery copy is read-only.',
      };
    const parsed = normalizeDefaults(settingsSchema.parse(settings));
    const old = this.read(guildId).settings;
    const result = this.ctx.storage.transactionSync(() => {
      const row = this.identity(guildId);
      if (row.revision !== expectedRevision) return { ok: false as const, revision: row.revision };
      try {
        this.playStore.assertSettings(this.read(guildId).settings, parsed);
      } catch (error) {
        if (!(error instanceof PlayError)) throw error;
        return { ok: false as const, revision: row.revision, reason: error.message };
      }
      const sql = this.ctx.storage.sql;
      const queueIds = new Set(parsed.queues.map((q) => q.id)),
        gameIds = new Set(parsed.games.map((g) => g.id));
      for (const q of sql.exec<{ id: string }>('SELECT id FROM queues').toArray())
        if (!queueIds.has(q.id)) sql.exec('DELETE FROM queues WHERE id=?', q.id);
      for (const g of parsed.games)
        sql.exec(
          'INSERT INTO games VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET config=excluded.config WHERE config!=excluded.config',
          g.id,
          JSON.stringify(g),
        );
      for (const q of parsed.queues)
        sql.exec(
          'INSERT INTO queues VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET game_id=excluded.game_id, config=excluded.config WHERE config!=excluded.config',
          q.id,
          q.gameId,
          JSON.stringify(q),
        );
      for (const g of sql.exec<{ id: string }>('SELECT id FROM games').toArray())
        if (!gameIds.has(g.id)) sql.exec('DELETE FROM games WHERE id=?', g.id);
      const revision = row.revision + 1;
      sql.exec('UPDATE community SET revision=? WHERE id=1', revision);
      sql.exec(
        'INSERT INTO settings_audit VALUES (?, ?, ?, ?)',
        revision,
        actorId,
        action,
        new Date().toISOString(),
      );
      return { ok: true as const, snapshot: this.read(guildId) };
    });
    if (result.ok) {
      this.ctx.storage.transactionSync(() => this.playStore.configure(result.snapshot.settings));
      this.playStore.reconcileLobbies();
      this.recordStore.configure(guildId, old);
      await this.playStore.schedule();
    }
    return result;
  }

  audit(guildId: string) {
    this.identity(guildId);
    return this.ctx.storage.sql
      .exec('SELECT * FROM settings_audit ORDER BY revision DESC LIMIT 20')
      .toArray();
  }
  play(guildId: string, action: Action) {
    return this.playStore.act(guildId, action);
  }
  playerMatch(
    guildId: string,
    channelId: string,
    actorId: string,
    matchId?: string,
    version?: number,
  ) {
    try {
      return {
        ok: true as const,
        ...this.playStore.playerMatch(guildId, channelId, actorId, matchId, version),
      };
    } catch (error) {
      if (!(error instanceof PlayError)) throw error;
      return { ok: false as const, content: error.message };
    }
  }
  prediction(
    guild: string,
    channel: string,
    actor: string,
    id?: string,
    book = false,
    allowExisting = false,
  ) {
    try {
      return {
        ok: true as const,
        ...(book
          ? this.playStore.predictionBook(guild, channel, actor, id!, allowExisting)
          : this.playStore.prediction(guild, channel, actor, id, allowExisting)),
      };
    } catch (error) {
      if (!(error instanceof PlayError)) throw error;
      return { ok: false as const, content: error.message };
    }
  }
  goldView(
    guild: string,
    channel: string,
    actor: string,
    view?: string,
    queue?: string,
    admin = false,
  ) {
    try {
      return {
        ok: true as const,
        content: this.playStore.goldView(guild, channel, actor, view, queue, admin),
      };
    } catch (error) {
      if (!(error instanceof PlayError)) throw error;
      return { ok: false as const, content: error.message };
    }
  }
  predictionReceipt(
    guild: string,
    input: Pick<
      Action,
      'id' | 'actor' | 'channelId' | 'admin' | 'kind' | 'bookId' | 'matchId' | 'side' | 'stake'
    >,
  ) {
    return this.playStore.predictionReceipt(guild, input);
  }
  async rally(guild: string, a: Action, voiceChannel?: string) {
    try {
      return await this.playStore.rally(guild, a, voiceChannel);
    } catch (error) {
      return {
        content:
          error instanceof PlayError ? error.message : 'The voice list could not be verified.',
        ok: false,
      };
    }
  }
  playerReceipt(
    guildId: string,
    input: Pick<Action, 'id' | 'actor' | 'channelId' | 'admin' | 'kind' | 'matchId' | 'version'> & {
      targetId?: string;
    },
  ) {
    return this.playStore.playerReceipt(guildId, input);
  }
  flush() {
    return this.playStore.flush();
  }
  records(guildId: string, gameId?: string, playersPage = 0) {
    return this.playStore.records(guildId, gameId, playersPage);
  }
  statistics(guild: string, game: string, user?: string, page = 0, admin = false, viewer?: string) {
    return this.recordStore.statistics(guild, game, user, page, admin, viewer);
  }
  importPreview(guild: string, bundle: ImportBundle) {
    return this.recordStore.preview(guild, bundle);
  }
  async importApply(guild: string, bundle: ImportBundle, target: string, actor: string) {
    if (this.ctx.storage.kv.get('recoveryReadOnly'))
      return { ok: false as const, error: 'Recovery copy is read-only.', status: 409 };
    try {
      const result = await this.recordStore.apply(guild, bundle, target, actor);
      await this.playStore.schedule();
      return result;
    } catch (error) {
      return {
        ok: false as const,
        error:
          error instanceof PlayError
            ? error.message
            : 'Storage could not confirm the import. Preview again before retrying.',
        status: error instanceof PlayError ? 409 : 500,
      };
    }
  }
  async linkGameProfile(guild: string, input: RecordAction, actor: string, admin: boolean) {
    this.identity(guild);
    if (this.ctx.storage.kv.get('recoveryReadOnly'))
      return { ok: false, content: 'Recovery copy is read-only.' };
    const action = recordActionSchema.parse(input);
    if (
      !['link', 'reassign'].includes(action.kind) ||
      (!admin && (action.userId !== actor || action.kind !== 'link'))
    )
      return { ok: false, content: 'Only organizers can assign another player’s profile.' };
    const game = this.read(guild).settings.games.find((g) => g.id === action.gameId);
    const adapter = game?.connector?.enabled ? gameAdapter(game.catalogId) : undefined;
    if (!adapter || !action.externalId)
      return { ok: false, content: 'Enable an available game connector first.' };
    const found = await adapter.profile(action.externalId);
    if (found.status !== 'ok')
      return {
        ok: false,
        content:
          found.status === 'not_found'
            ? 'Public profile not found. Check its ID.'
            : 'Profile provider unavailable; nothing was changed. Try again later.',
      };
    try {
      const current = this.read(guild).settings.games.find((g) => g.id === action.gameId);
      if (!current?.connector?.enabled || current.catalogId !== adapter.id)
        throw new PlayError('Game settings changed. Try again.');
      const result = this.recordStore.action(
        guild,
        {
          ...action,
          provider: adapter.profileProvider,
          url: found.value.url,
          externalId: found.value.id,
        },
        actor,
        admin,
        true,
      );
      this.playStore.reconcileLobbies(action.gameId);
      await this.playStore.schedule();
      return result;
    } catch (error) {
      return {
        ok: false,
        content:
          error instanceof PlayError ? error.message : 'Profile could not be saved. Try again.',
      };
    }
  }
  async recordAction(guild: string, action: RecordAction, actor: string, admin: boolean) {
    if (this.ctx.storage.kv.get('recoveryReadOnly'))
      return { ok: false as const, content: 'Recovery copy is read-only.', status: 409 };
    try {
      const result = this.recordStore.action(guild, action, actor, admin);
      if (['link', 'unlink', 'reassign'].includes(action.kind))
        this.playStore.reconcileLobbies(action.gameId);
      await this.playStore.schedule();
      return result;
    } catch (error) {
      return {
        ok: false as const,
        content:
          error instanceof PlayError
            ? error.message
            : 'Storage could not confirm the player action. Reload records before retrying.',
        status: error instanceof PlayError ? 409 : 500,
      };
    }
  }
  async alarm() {
    if (this.ctx.storage.kv.get('recoveryReadOnly')) {
      await this.ctx.storage.deleteAlarm();
      return;
    }
    const row = this.ctx.storage.sql
      .exec<{ guild_id: string }>('SELECT guild_id FROM community WHERE id=1')
      .toArray()[0];
    if (row) await this.playStore.alarm(row.guild_id);
  }
  async operationalBackup(guild: string) {
    this.identity(guild);
    const tables = this.ctx.storage.transactionSync(() => {
      const result: Record<string, Record<string, SqlStorageValue>[]> = {};
      for (const { name } of this.ctx.storage.sql.exec<{ name: string }>(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '__cf_%' ORDER BY name",
      )) {
        if (name.startsWith('_')) continue;
        if (!/^[a-z_]+$/.test(name)) throw Error('Unexpected table identity.');
        const rows = this.ctx.storage.sql.exec(`SELECT * FROM ${name} LIMIT 50001`).toArray();
        if (rows.length > 50000) throw Error('Use paged operational export for this community.');
        result[name] = rows;
      }
      return result;
    });
    return {
      format: 1,
      communityId: guild,
      sourceApplicationId:
        this.ctx.storage.kv.get<string>('recoverySourceApplication') ??
        this.env.DISCORD_APPLICATION_ID,
      createdAt: new Date().toISOString(),
      tables,
    };
  }
  async operationalRestore(
    guild: string,
    backup: {
      format: number;
      communityId: string;
      sourceApplicationId: string;
      tables: Record<string, Record<string, SqlStorageValue>[]>;
    },
  ) {
    try {
      if (this.env.APP_ENV === 'production')
        throw Error('Restore only into an isolated recovery deployment.');
      if (backup.format !== 1 || backup.communityId !== guild)
        throw Error('Backup identity mismatch.');
      if (this.ctx.storage.sql.exec('SELECT 1 FROM community').toArray().length)
        throw Error('Restore requires an empty isolated community object.');
      this.ctx.storage.transactionSync(() => {
        const sql = this.ctx.storage.sql;
        const known = new Set(
          sql
            .exec<{ name: string }>(
              "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '__cf_%'",
            )
            .toArray()
            .map((r) => r.name)
            .filter((name) => !name.startsWith('_')),
        );
        const required = [
          'community',
          'games',
          'queues',
          'matches',
          'profiles',
          'gold_ledger',
          'wallets',
          'schema_version',
        ];
        if (required.some((name) => !Array.isArray(backup.tables[name])))
          throw Error('Incomplete backup manifest.');
        const sourceVersion = backup.tables.schema_version[0]?.version;
        if (
          [5, 6, 7].includes(Number(sourceVersion)) &&
          [...known]
            .filter(
              (name) =>
                Number(sourceVersion) >= 7 ||
                ![
                  'civ_rounds',
                  'civ_pick_history',
                  'identity_rejections',
                  'played_selections',
                  'bet_start_evidence',
                ].includes(name),
            )
            .some((name) => !Array.isArray(backup.tables[name]))
        )
          throw Error('Incomplete backup manifest; all schema-5/6 community tables are required.');
        for (const [table, rows] of Object.entries(backup.tables)) {
          if (
            table.startsWith('_') ||
            !known.has(table) ||
            !/^[a-z_]+$/.test(table) ||
            !Array.isArray(rows) ||
            rows.length > 50000
          )
            throw Error('Unsupported backup table.');
          const columns = new Set(
            sql
              .exec<{ name: string }>(`PRAGMA table_info(${table})`)
              .toArray()
              .map((r) => r.name),
          );
          sql.exec(`DELETE FROM ${table}`);
          for (const row of rows) {
            const names = Object.keys(row);
            if (!names.length || names.some((n) => !columns.has(n) || !/^[a-z_]+$/.test(n)))
              throw Error('Unsupported backup column.');
            sql.exec(
              `INSERT INTO ${table}(${names.join(',')}) VALUES(${names.map(() => '?').join(',')})`,
              ...names.map((n) => row[n]),
            );
          }
        }
        const identity = sql
          .exec<{ guild_id: string }>('SELECT guild_id FROM community WHERE id=1')
          .toArray()[0];
        if (identity?.guild_id !== guild) throw Error('Backup community row mismatch.');
        const version = sql
          .exec<{ version: number }>('SELECT version FROM schema_version')
          .toArray()[0]?.version;
        if (![4, 5, 6, 7].includes(version!)) throw Error('Unsupported backup schema version.');
        const imbalance = sql
          .exec(
            'SELECT w.user_id FROM wallets w LEFT JOIN gold_ledger l ON l.game_id=w.game_id AND l.user_id=w.user_id GROUP BY w.game_id,w.user_id HAVING w.balance != COALESCE(SUM(l.amount),0) LIMIT 1',
          )
          .toArray();
        if (imbalance.length) throw Error('Backup ledger does not reconcile with wallets.');
        this.ctx.storage.kv.put('recoveryReadOnly', true);
        this.ctx.storage.kv.put('recoverySourceApplication', backup.sourceApplicationId);
      });
      await this.ctx.storage.deleteAlarm();
      return {
        restored: true,
        readOnly: true,
        discordDelivery: false,
        tables: Object.keys(backup.tables).length,
      };
    } catch (error) {
      return {
        restored: false,
        error: error instanceof Error ? error.message : 'Restore validation failed.',
      };
    }
  }
  async operationalCheckpoint(guild: string) {
    this.identity(guild);
    return {
      communityId: guild,
      bookmark: await this.ctx.storage.getCurrentBookmark(),
      createdAt: new Date().toISOString(),
    };
  }
  economyImport(guild: string, game: string, bundle: EconomyImport, digest: string, apply = false) {
    this.identity(guild);
    if (this.ctx.storage.kv.get('recoveryReadOnly'))
      return { ok: false, error: 'Recovery copy is read-only.' };
    if (!this.read(guild).settings.games.some((g) => g.id === game))
      return { ok: false, error: 'Configure the destination game first.' };
    try {
      return this.ctx.storage.transactionSync(() => ({
        ok: true,
        ...(apply
          ? new EconomyStore(this.ctx.storage.sql).importApply(game, bundle, digest)
          : { counts: new EconomyStore(this.ctx.storage.sql).importPreview(game, bundle) }),
      }));
    } catch (error) {
      return {
        ok: false,
        error:
          error instanceof PlayError ? error.message : 'Economy import could not be committed.',
      };
    }
  }
  async pollLobbies(guild: string) {
    this.identity(guild);
    if (this.ctx.storage.kv.get('recoveryReadOnly')) return;
    await this.playStore.pollLobbies(guild);
  }
  async connectorEnded(guild: string, event: LobbyEvent) {
    this.identity(guild);
    if (this.ctx.storage.kv.get('recoveryReadOnly'))
      return { accepted: false, reason: 'recovery_read_only' };
    const result = this.playStore.connectorEnded(guild, event);
    await this.playStore.schedule();
    return result;
  }
}

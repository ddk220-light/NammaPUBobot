import { runtimeAdapter, connectorAvailability } from '../games/runtime';
import { operations, type ProviderProfile } from '../games/types';
import { supportsOwnership, type GameAccountProof } from '../auth/game-accounts';
import { parseSteamAccountReference } from '../games/manual';
import { recordActionSchema } from '../domain/records';
import type { LobbyEvent } from '../domain/lobby';
import { DurableObject } from 'cloudflare:workers';
import type { Env } from '../env';
import {
  settingsSchema,
  normalizeDefaults,
  snowflake,
  type Settings,
  type Snapshot,
} from '../domain/config';
import { createPlayTables, PlayStore } from './play-store';
import { PlayError, type Action, type Match } from '../domain/play';
import {
  createRecordTables,
  backfillProfileClaims,
  RecordStore,
  type VerifiedAccountClaim,
} from './record-store';
import type { ImportBundle, RecordAction } from '../domain/records';
import { EconomyStore } from './economy-store';
import { adminRequestSchema, type AdminRequest } from '../domain/admin';
import type { EconomyImport } from '../domain/economy-import';
import { testRequestSchema, type RetiredTestSession } from '../domain/testing';
import { testMembers } from '../discord/test-members';

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
      if (version && ![1, 2, 3, 4, 5, 6, 7, 8].includes(version))
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
      sql.exec('UPDATE schema_version SET version=8');
    });
    this.recordStore = new RecordStore(ctx, env, (guild) => this.read(guild));
    if (!ctx.storage.kv.get('testMode')) this.recordStore.backfill();
    this.playStore = new PlayStore(ctx, env, (guild) => this.read(guild), this.recordStore);
    const retiredTest = ctx.storage.kv.get<RetiredTestSession>('testSession');
    if (ctx.storage.kv.get('testMode') && retiredTest) {
      retiredTest.expiresAt = 0;
      ctx.storage.kv.put('testSession', retiredTest);
    }
    // Adopt periodic queue visibility for existing communities on their next wake.
    ctx.blockConcurrencyWhile(() => this.playStore.schedule());
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
    action: 'save' | 'enable_aoe2_features' | 'voice_preview',
  ) {
    if (this.ctx.storage.kv.get('recoveryReadOnly'))
      return {
        ok: false as const,
        revision: this.identity(guildId).revision,
        reason: 'Recovery copy is read-only.',
      };
    const parsed = normalizeDefaults(settingsSchema.parse(settings));
    if (parsed.queues.some((q) => q.adminRoles?.includes(guildId)))
      return {
        ok: false as const,
        revision: this.identity(guildId).revision,
        reason: 'The @everyone role cannot administer queues.',
      };
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
      this.ctx.storage.transactionSync(() =>
        this.playStore.configure(result.snapshot.settings, old),
      );
      this.playStore.reconcileLobbies();
      if (!this.ctx.storage.kv.get('testMode')) this.recordStore.configure(guildId, old);
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

  async setVoicePreview(
    guild: string,
    expectedRevision: number,
    queueIds: string[],
    enabled: boolean,
    apply: boolean,
  ) {
    if (this.env.APP_ENV !== 'staging' || this.ctx.storage.kv.get('recoveryReadOnly'))
      return { ok: false as const, reason: 'Use the active staging community.' };
    const before = this.read(guild);
    if (before.revision !== expectedRevision)
      return { ok: false as const, reason: 'Settings changed. Preview again.' };
    const settings = structuredClone(before.settings),
      queues = settings.queues.filter((q) => queueIds.includes(q.id));
    if (
      !queueIds.length ||
      queues.length !== new Set(queueIds).size ||
      (enabled && queues.some((q) => !q.enabled))
    )
      return { ok: false as const, reason: 'Choose existing enabled queues.' };
    for (const q of queues) q.voicePreview = enabled;
    if (!apply || JSON.stringify(settings) === JSON.stringify(before.settings))
      return { ok: true as const, applied: false, revision: before.revision, queues };
    const result = await this.save(guild, expectedRevision, settings, 'operator', 'voice_preview');
    if (!result.ok) return result;
    return { ok: true as const, applied: true, revision: result.snapshot.revision, queues };
  }

  /** Narrow operator rollout: existing AoE2 queues only, no arbitrary settings or player actions. */
  async enableAoE2Features(
    guild: string,
    expectedRevision: number,
    queueIds: string[],
    lobbyName: string,
    apply: boolean,
  ) {
    const before = this.read(guild);
    if (this.ctx.storage.kv.get('recoveryReadOnly'))
      return { ok: false as const, reason: 'Recovery copy is read-only.' };
    if (before.revision !== expectedRevision)
      return { ok: false as const, reason: 'Settings changed. Preview again before applying.' };
    const settings = structuredClone(before.settings);
    const game = settings.games.find((g) => g.catalogId === 'aoe2');
    const queues = settings.queues.filter((q) => queueIds.includes(q.id));
    if (
      !game ||
      !queueIds.length ||
      queues.length !== new Set(queueIds).size ||
      queues.some((q) => q.gameId !== game.id || !q.enabled)
    )
      return { ok: false as const, reason: 'Choose existing enabled AoE2 queues.' };
    if (
      this.ctx.storage.sql
        .exec(
          "SELECT 1 FROM matches WHERE game_id=? AND state IN ('playing','check_in') LIMIT 1",
          game.id,
        )
        .toArray().length
    )
      return { ok: false as const, reason: 'Finish active AoE2 matches before enabling features.' };
    game.connector = { enabled: true, autoAttach: { lobbyName } };
    for (const q of queues)
      Object.assign(q.modules, {
        civs: true,
        stories: true,
        gold: true,
        predictions: true,
        maps: true,
      });
    const parsed = settingsSchema.parse(settings);
    const changes = {
      game: parsed.games.find((g) => g.id === game.id)!,
      queues: parsed.queues.filter((q) => queueIds.includes(q.id)),
    };
    if (!apply || JSON.stringify(parsed) === JSON.stringify(before.settings))
      return { ok: true as const, applied: false, revision: before.revision, changes };
    const result = await this.save(
      guild,
      expectedRevision,
      parsed,
      'operator',
      'enable_aoe2_features',
    );
    if (!result.ok) return result;
    return { ok: true as const, applied: true, revision: result.snapshot.revision, changes };
  }
  play(guildId: string, action: Action) {
    return this.playStore.act(guildId, action);
  }
  async testCommand(guild: string, input: unknown) {
    try {
      const request = testRequestSchema.parse(input);
      const { queue } = this.playStore.testContext(guild, request);
      const previous = this.playStore.testReceipt(request);
      if (previous) return previous;
      let members: Action['actor'][] = [];
      if (request.op === 'populate') {
        const known = this.ctx.storage.sql
          .exec<{ user_id: string }>(
            'SELECT user_id FROM profiles WHERE game_id=? UNION SELECT user_id FROM queue_entries LIMIT 1000',
            queue.gameId,
          )
          .toArray()
          .map((r) => r.user_id);
        members = await testMembers(
          this.env,
          guild,
          request.channelId,
          request.count ?? 8,
          known,
          (member) => this.playStore.testEligible(queue, member),
        );
      }
      const reply = await this.playStore.testAction(guild, request, members);
      this.ctx.waitUntil(this.playStore.flush());
      return reply;
    } catch (error) {
      return {
        ok: false,
        content:
          error instanceof PlayError
            ? error.message
            : 'The test command could not be confirmed. Check the regular queue or match card before retrying.',
      };
    }
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
  async pingVoice(guild: string, a: Action) {
    try {
      return { ok: true, ...(await this.playStore.pingVoice(guild, a)) };
    } catch (error) {
      return {
        ok: false,
        content:
          error instanceof PlayError
            ? error.message
            : 'The voice list could not be verified. Try again shortly.',
      };
    }
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
    try {
      const action = recordActionSchema.parse(input);
      if (
        !['link', 'reassign'].includes(action.kind) ||
        (!admin && (action.userId !== actor || action.kind !== 'link'))
      )
        throw new PlayError('Only organizers can assign another player’s profile.');
      const game = this.read(guild).settings.games.find((g) => g.id === action.gameId);
      const adapter = runtimeAdapter(this.env, game?.catalogId, game?.connector?.capabilities);
      if (!adapter || !action.externalId)
        throw new PlayError('Choose a registered game and account ID.');
      const id = adapter.parseAccountReference?.(action.externalId) ?? action.externalId.trim();
      // profileUrl performs the provider-specific identifier validation even in manual mode.
      const declaredUrl = adapter.profileUrl(id);
      const availability =
        game?.connector?.capabilities?.accountLookup === false
          ? 'disabled'
          : connectorAvailability(this.env, adapter, 'accountLookup');
      const found =
        availability === 'available'
          ? await adapter.profile(id)
          : {
              status:
                availability === 'credentials_required'
                  ? ('authentication_required' as const)
                  : ('unsupported' as const),
            };
      if (found.status === 'not_found')
        throw new PlayError(
          'Public profile not found. Check its ID or use /profile link to declare an unsupported identity.',
        );
      if (found.status === 'ok' && found.value.id !== id)
        throw new PlayError('The provider returned a different account. Nothing was linked.');
      const current = this.read(guild).settings.games.find((g) => g.id === action.gameId);
      if (
        !current ||
        current.catalogId !== adapter.id ||
        this.ctx.storage.kv.get('recoveryReadOnly')
      )
        throw new PlayError('Game settings changed. Try again.');
      const url = found.status === 'ok' ? found.value.url : declaredUrl;
      const result = this.recordStore.action(
        guild,
        {
          ...action,
          provider: adapter.profileProvider,
          externalId: id,
          ...(url ? { url } : { url: undefined }),
        },
        actor,
        admin,
        found.status === 'ok',
      );
      this.playStore.reconcileLobbies(action.gameId);
      await this.playStore.schedule();
      return found.status === 'ok'
        ? result
        : {
            ...result,
            content: `${result.content} Provider lookup: ${(availability === 'available' ? found.status : availability).replaceAll('_', ' ')}. Manual play remains available.`,
          };
    } catch (error) {
      return {
        ok: false,
        content: error instanceof Error ? error.message : 'Profile could not be saved. Try again.',
      };
    }
  }
  /** Internal RPC target for the authenticated, session-bound identity callback. */
  async linkVerifiedGameAccount(guild: string, proof: GameAccountProof) {
    this.identity(guild);
    if (this.ctx.storage.kv.get('recoveryReadOnly'))
      return { ok: false, content: 'Recovery copy is read-only.' };
    try {
      const game = this.read(guild).settings.games.find((g) => g.id === proof.gameId);
      const adapter = runtimeAdapter(this.env, game?.catalogId, game?.connector?.capabilities);
      const provider = proof.provider === 'battle-net' ? 'battlenet' : proof.provider;
      if (
        proof.communityId !== guild ||
        !adapter ||
        game?.catalogId !== proof.catalogId ||
        !supportsOwnership(adapter.id, provider) ||
        !snowflake.safeParse(proof.discordUserId).success ||
        !Number.isSafeInteger(proof.verifiedAt) ||
        proof.verifiedAt > Date.now() + 30000 ||
        Date.now() - proof.verifiedAt > 15 * 60000
      )
        throw new PlayError(
          'Account verification does not match this community, game or current session.',
        );
      const expected = {
        steam: 'steam-openid',
        'battle-net': 'battlenet-oauth',
        bar: 'bar-oauth',
      } as const;
      if (proof.verifiedBy !== expected[proof.provider])
        throw new PlayError('Unexpected ownership verification method.');
      if (
        game.connector?.capabilities?.ownershipVerification === false ||
        connectorAvailability(this.env, adapter, 'ownershipVerification') !== 'available'
      )
        throw new PlayError(
          'Ownership verification is disabled or requires credentials. Declared linking remains available.',
        );
      const claims: VerifiedAccountClaim[] = [];
      let linkedNative = false;
      if (proof.provider === 'steam') {
        const steamId = parseSteamAccountReference(proof.externalId);
        claims.push({
          gameId: game.id,
          provider: 'steam',
          externalId: steamId,
          userId: proof.discordUserId,
          url: `https://steamcommunity.com/profiles/${steamId}`,
          edition: adapter.manifest.edition,
          namespace: 'steam',
          verifiedBy: proof.verifiedBy,
          verifiedAt: proof.verifiedAt,
          platformId: steamId,
        });
        if (adapter.profileProvider === 'steam') linkedNative = true;
        else if (
          game.connector?.capabilities?.accountLookup !== false &&
          connectorAvailability(this.env, adapter, 'accountLookup') === 'available'
        ) {
          const candidates: ProviderProfile[] = [];
          if (adapter.resolveSteam) {
            const resolved = await adapter.resolveSteam(steamId);
            if (resolved.status === 'ok' && resolved.value.platformId === steamId)
              candidates.push(resolved.value);
          }
          // Companion exposes a native profile's Steam ID even when reverse lookup is absent.
          const existing = this.recordStore
            .references(game.id, proof.discordUserId)
            .filter((r) => r.provider === adapter.profileProvider && r.trust !== 'suggested');
          if (!candidates.length)
            for (const account of existing.slice(0, 8)) {
              const found = await adapter.profile(account.external_id);
              if (
                found.status === 'ok' &&
                found.value.id === account.external_id &&
                found.value.platformId === steamId
              )
                candidates.push(found.value);
            }
          const unique = [...new Map(candidates.map((p) => [p.id, p])).values()];
          // Ambiguous mappings remain declarations; a Steam proof alone does not select a character.
          if (unique.length === 1) {
            const native = unique[0];
            claims.push({
              gameId: game.id,
              provider: adapter.profileProvider,
              externalId: native.id,
              userId: proof.discordUserId,
              url: native.url,
              edition: adapter.manifest.edition,
              namespace: adapter.manifest.accountNamespace,
              verifiedBy: proof.verifiedBy,
              verifiedAt: proof.verifiedAt,
              platformId: steamId,
            });
            linkedNative = true;
          }
        }
      } else {
        const id = adapter.parseAccountReference?.(proof.externalId) ?? proof.externalId;
        claims.push({
          gameId: game.id,
          provider: adapter.profileProvider,
          externalId: id,
          userId: proof.discordUserId,
          url: adapter.profileUrl(id),
          edition: adapter.manifest.edition,
          namespace: adapter.manifest.accountNamespace,
          verifiedBy: proof.verifiedBy,
          verifiedAt: proof.verifiedAt,
          platformId: proof.platformId,
        });
        linkedNative = true;
      }
      const current = this.read(guild).settings.games.find((g) => g.id === proof.gameId);
      if (
        current?.catalogId !== adapter.id ||
        current.connector?.capabilities?.ownershipVerification === false ||
        connectorAvailability(this.env, adapter, 'ownershipVerification') !== 'available' ||
        this.ctx.storage.kv.get('recoveryReadOnly')
      )
        throw new PlayError('Game settings changed. Start verification again.');
      if (
        proof.provider === 'steam' &&
        adapter.profileProvider !== 'steam' &&
        (current.connector?.capabilities?.accountLookup === false ||
          connectorAvailability(this.env, adapter, 'accountLookup') !== 'available')
      ) {
        claims.splice(1);
        linkedNative = false;
      }
      this.recordStore.verifyAccounts(guild, claims, proof.discordUserId);
      this.playStore.reconcileLobbies(game.id);
      await this.playStore.schedule();
      return {
        ok: true,
        content: linkedNative
          ? 'Account ownership verified. Existing ratings and participation are unchanged.'
          : 'Steam ownership verified. The native game account could not be established; existing native trust was not changed. Manual play remains available.',
      };
    } catch (error) {
      return {
        ok: false,
        content:
          error instanceof PlayError
            ? error.message
            : 'Ownership could not be saved. Start verification again.',
      };
    }
  }
  connectorStatus(guild: string) {
    const settings = this.read(guild).settings;
    const lastObserved = new Map(
      this.ctx.storage.sql
        .exec<{ game_id: string; last_success: number }>(
          "SELECT game_id,MAX(json_extract(config,'$.lobby.lastSuccessAt')) AS last_success FROM matches WHERE json_type(config,'$.lobby.lastSuccessAt')='integer' GROUP BY game_id",
        )
        .toArray()
        .map((r) => [r.game_id, r.last_success]),
    );
    const matches = this.ctx.storage.sql
      .exec<{ config: string }>("SELECT config FROM matches WHERE state IN ('check_in','playing')")
      .toArray()
      .map((r) => JSON.parse(r.config) as Match);
    return {
      games: settings.games.map((game) => {
        const adapter = runtimeAdapter(this.env, game.catalogId);
        const bindings = matches
          .filter((m) => m.queue.gameId === game.id)
          .map((m) => m.lobby)
          .filter((b) => !!b);
        const lastSuccessAt = lastObserved.get(game.id);
        const capabilities = Object.fromEntries(
          operations.map((operation) => [
            operation,
            !adapter || adapter.manifest.capabilities[operation] === 'unsupported'
              ? 'unsupported'
              : game.connector?.capabilities?.[operation] === false ||
                  (!game.connector?.enabled &&
                    ['lobbyDiscovery', 'lobbyObservation'].includes(operation))
                ? 'disabled'
                : connectorAvailability(this.env, adapter, operation),
          ]),
        );
        return {
          gameId: game.id,
          catalogId: game.catalogId,
          capabilities,
          lastSuccessAt,
          failures: bindings.reduce((n, b) => n + b.failures, 0),
          unresolvedBindings: bindings.filter((b) => !b.rosterVerified && !b.confirmedBy).length,
          ...(lastSuccessAt ? { observationDelayMs: Math.max(0, Date.now() - lastSuccessAt) } : {}),
        };
      }),
    };
  }
  async refreshLobbyReplays(guild: string, action: Action) {
    this.identity(guild);
    if (this.ctx.storage.kv.get('recoveryReadOnly'))
      return { ok: false, content: 'Recovery copy is read-only.' };
    return this.playStore.refreshLobbyReplays(guild, action);
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
  async adminPanel(guild: string, input: AdminRequest) {
    try {
      const request = adminRequestSchema.parse(input);
      const view = await this.playStore.adminPanel(guild, request);
      return { ok: true as const, ...view };
    } catch (error) {
      return {
        ok: false as const,
        content:
          error instanceof PlayError
            ? error.message
            : 'The admin panel could not be loaded. Try /admin again.',
      };
    }
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
          [5, 6, 7, 8].includes(Number(sourceVersion)) &&
          [...known]
            .filter(
              (name) =>
                (name !== 'profile_claims' || Number(sourceVersion) >= 8) &&
                (Number(sourceVersion) >= 7 ||
                  ![
                    'civ_rounds',
                    'civ_pick_history',
                    'identity_rejections',
                    'played_selections',
                    'bet_start_evidence',
                  ].includes(name)),
            )
            .some((name) => !Array.isArray(backup.tables[name]))
        )
          throw Error('Incomplete backup manifest; all tables for the source schema are required.');
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
        if (![4, 5, 6, 7, 8].includes(version!)) throw Error('Unsupported backup schema version.');
        backfillProfileClaims(sql);
        sql.exec('UPDATE schema_version SET version=8');
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

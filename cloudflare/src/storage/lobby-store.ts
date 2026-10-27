import { observeGamePlayers } from './game-records';
import { gameCapabilities } from '../games/catalog';
import type { Settings } from '../domain/config';
import type { Action, Match } from '../domain/play';
import { PlayError } from '../domain/errors';
import {
  lobbyEventSchema,
  lobbySummary,
  rosterMatches,
  validObservation,
  type LobbyBinding,
  type LobbyEvent,
} from '../domain/lobby';
import { gameAdapter } from '../games/registry';
import type { ProviderResult, LobbyObservation } from '../games/types';

interface Hooks {
  occupancy?(id: string): Promise<LobbyObservation | null>;
  started?(match: Match, observation: LobbyObservation, eventId: string): void;
  match(id: string): Match;
  save(match: Match): void;
  prompt(match: Match, event: LobbyEvent): void;
  audit(action: string, actor: string, details: unknown, match: Match): void;
}
const active = (m: Match) =>
  (['check_in', 'playing'].includes(m.state) &&
    Date.now() < (m.expiresAt ?? m.createdAt + 43200000)) ||
  (m.state === 'finished' && !!m.closedAt && Date.now() < m.closedAt + 900000);
const complete = (m: Match) =>
  !!m.lobby?.observation?.finishedAt &&
  (Date.now() > m.lobby.observation.finishedAt + 900000 ||
    m.lobby.profiles.every(
      (p) =>
        !p.profileId ||
        !!m.lobby?.observation?.players.find((x) => x.profileId === p.profileId)?.selection,
    ));

/** Durable orchestration shared by all games. Adapters only perform bounded network reads. */
export class LobbyStore {
  constructor(
    private ctx: DurableObjectState,
    private settings: () => Settings,
    private hooks: Hooks,
  ) {}
  private get sql() {
    return this.ctx.storage.sql;
  }
  private profiles(m: Match, provider: string) {
    return m.teams.flatMap((team, side) =>
      team.map((p) => {
        const refs = this.sql
          .exec<{ external_id: string }>(
            'SELECT external_id FROM profile_refs WHERE game_id=? AND provider=? AND user_id=? ORDER BY external_id',
            m.queue.gameId,
            provider,
            p.id,
          )
          .toArray();
        if (refs.length > 1)
          throw new PlayError(
            'A player has several profiles for this provider. Remove ambiguous links before attaching.',
          );
        return { userId: p.id, profileId: refs[0]?.external_id ?? null, side };
      }),
    );
  }
  private enabled(m: Match) {
    const game = this.settings().games.find((g) => g.id === m.queue.gameId);
    return game?.connector?.enabled ? gameAdapter(game.catalogId) : undefined;
  }
  action(m: Match, a: Action) {
    if (a.kind === 'lobby_status') return { ok: true, content: lobbySummary(m.lobby) };
    if (!['check_in', 'playing'].includes(m.state) || !active(m))
      throw new PlayError('This match is closed.');
    if (!a.admin && !m.captains.includes(a.actor.id))
      throw new PlayError('Only a captain or administrator can attach or unlink a lobby.');
    if (a.kind === 'lobby_unlink') {
      if (m.lobby) {
        m.lobby.state = 'unlinked';
        m.lobby.reason = 'Unlinked by a captain or administrator.';
      }
      this.hooks.save(m);
      this.hooks.audit('lobby_unlinked', a.actor.id, {}, m);
      return { ok: true, content: 'Lobby unlinked. Manual reporting remains available.' };
    }
    if (m.state !== 'playing')
      throw new PlayError('Complete check-in and map selection before attaching a lobby.');
    const adapter = this.enabled(m);
    if (!adapter)
      throw new PlayError(
        'Enable an available lobby connector in this game’s settings first. Manual play remains available.',
      );
    let externalId: string;
    try {
      externalId = adapter.parseLobbyReference(a.reference ?? '');
    } catch (error) {
      throw new PlayError(error instanceof Error ? error.message : 'Invalid lobby reference.');
    }
    if (
      m.lobby?.externalId === externalId &&
      ['pending', 'observed', 'unavailable'].includes(m.lobby.state)
    )
      return { ok: true, content: lobbySummary(m.lobby) };
    const others = this.sql
      .exec<{ config: string }>(
        "SELECT config FROM matches WHERE game_id=? AND state IN ('playing','check_in') AND id!=?",
        m.queue.gameId,
        m.id,
      )
      .toArray();
    if (
      others.some((row) => {
        const other: Match = JSON.parse(row.config);
        return (
          active(other) &&
          other.lobby?.externalId === externalId &&
          ['pending', 'observed', 'unavailable'].includes(other.lobby.state)
        );
      })
    )
      throw new PlayError(
        'That lobby is already attached to another active match in this community.',
      );
    m.lobby = {
      id: crypto.randomUUID(),
      adapterId: adapter.id,
      externalId,
      url: adapter.lobbyUrl(externalId),
      attachedAt: Date.now(),
      rosterVersion: m.rosterVersion ?? 0,
      profiles: this.profiles(m, adapter.profileProvider),
      state: 'pending',
      failures: 0,
      rosterVerified: false,
      prompted: false,
      occupancyAvailable: gameCapabilities(adapter.id)?.occupancy ?? false,
    };
    this.arm(m, Date.now());
    this.hooks.save(m);
    this.hooks.audit(
      'lobby_attached',
      a.actor.id,
      { bindingId: m.lobby.id, externalId, adapterId: adapter.id },
      m,
    );
    return { ok: true, content: lobbySummary(m.lobby) };
  }
  /** Called with every match save, including cancellation, reporting and roster changes. */
  sync(m: Match) {
    const b = m.lobby;
    if (!b) return;
    if (!active(m) && !['unlinked', 'invalidated'].includes(b.state)) {
      b.state = 'stopped';
      b.reason = 'Match closed or lifetime elapsed.';
    } else if (b.rosterVersion !== (m.rosterVersion ?? 0)) {
      b.state = 'invalidated';
      b.reason = 'Roster changed; explicitly attach the lobby again.';
    }
    if (['unlinked', 'invalidated', 'stopped'].includes(b.state) || complete(m))
      this.sql.exec('DELETE FROM deadlines WHERE key=?', `lobby:${m.id}`);
    else if (
      m.state === 'finished' &&
      !this.sql.exec('SELECT 1 FROM deadlines WHERE key=?', `lobby:${m.id}`).toArray().length
    )
      this.arm(m, Date.now() + 30000);
  }
  private arm(m: Match, due: number) {
    this.sql.exec(
      'INSERT OR REPLACE INTO deadlines VALUES(?,?,?,?)',
      `lobby:${m.id}`,
      'lobby',
      m.id,
      Math.min(
        due,
        m.state === 'finished' && m.closedAt
          ? m.closedAt + 900000
          : (m.expiresAt ?? m.createdAt + 43200000),
      ),
    );
  }
  private current(m: Match, bindingId: string): boolean {
    const b = m.lobby;
    if (!b || b.id !== bindingId || !['pending', 'observed', 'unavailable'].includes(b.state))
      return false;
    if (!active(m)) {
      this.hooks.save(m);
      return false;
    }
    const adapter = this.enabled(m);
    let sameProfiles = false;
    try {
      sameProfiles =
        !!adapter &&
        JSON.stringify(this.profiles(m, adapter.profileProvider)) === JSON.stringify(b.profiles);
    } catch {}
    if (
      !adapter ||
      adapter.id !== b.adapterId ||
      b.rosterVersion !== (m.rosterVersion ?? 0) ||
      !sameProfiles
    ) {
      b.state = 'invalidated';
      b.reason = 'Connector, roster or linked profiles changed; attach again after review.';
      this.hooks.save(m);
      return false;
    }
    return true;
  }
  reconcile(gameId?: string) {
    for (const row of this.sql
      .exec<{ config: string }>(
        `SELECT config FROM matches WHERE state IN ('playing','check_in')${gameId ? ' AND game_id=?' : ''}`,
        ...(gameId ? [gameId] : []),
      )
      .toArray()) {
      const m: Match = JSON.parse(row.config);
      if (m.lobby) this.current(m, m.lobby.id);
    }
  }
  accept(guild: string, input: LobbyEvent, providerRetryAt?: number) {
    const parsed = lobbyEventSchema.safeParse(input);
    if (!parsed.success || parsed.data.communityId !== guild)
      return { accepted: false, reason: 'invalid_binding' };
    const event = parsed.data;
    return this.ctx.storage.transactionSync(() => {
      let m: Match;
      try {
        m = this.hooks.match(event.matchId);
      } catch {
        return { accepted: false, reason: 'unknown_match' };
      }
      if (m.queue.gameId !== event.gameId || !this.current(m, event.bindingId))
        return { accepted: false, reason: 'stale_or_closed' };
      const b = m.lobby!,
        o = event.observation;
      if (
        event.adapterId !== b.adapterId ||
        event.rosterVersion !== b.rosterVersion ||
        o.externalId !== b.externalId ||
        !validObservation(o, Date.now())
      )
        return { accepted: false, reason: 'invalid_evidence' };
      if (o.startedAt !== null && o.startedAt < m.createdAt - 120000)
        return { accepted: false, reason: 'historical_game' };
      if (
        b.lastEventId === event.eventId ||
        (b.lastSuccessAt !== undefined && o.observedAt < b.lastSuccessAt)
      )
        return { accepted: false, reason: 'duplicate_or_old' };
      if (
        (b.observation?.startedAt && b.observation.startedAt !== o.startedAt) ||
        (b.observation?.finishedAt && b.observation.finishedAt !== o.finishedAt)
      )
        return { accepted: false, reason: 'conflicting_evidence' };
      const changed = b.observation?.phase !== o.phase || b.state !== 'observed';
      if (providerRetryAt !== undefined) b.providerRetryAt = providerRetryAt;
      b.state = 'observed';
      b.observation = o;
      b.lastSuccessAt = o.observedAt;
      b.lastEventId = event.eventId;
      b.failures = 0;
      const inferred = observeGamePlayers(
        this.sql,
        m,
        gameAdapter(b.adapterId)!.profileProvider,
        o,
      );
      if (inferred.length) {
        b.profiles = this.profiles(m, gameAdapter(b.adapterId)!.profileProvider);
        this.hooks.audit('profiles_inferred', 'connector', { links: inferred, bindingId: b.id }, m);
      }
      b.rosterVerified = rosterMatches(b, o);
      if (o.startedAt) this.hooks.started?.(m, o, event.eventId);
      if (o.phase === 'finished' && b.rosterVerified && m.state === 'playing' && !b.prompted) {
        b.prompted = true;
        this.hooks.prompt(m, event);
        this.hooks.audit(
          'game_end_prompt',
          'connector',
          { eventId: event.eventId, bindingId: b.id },
          m,
        );
      }
      if (changed)
        this.hooks.audit(
          'lobby_evidence',
          'connector',
          { phase: o.phase, source: o.source, rosterVerified: b.rosterVerified },
          m,
        );
      if (!complete(m))
        this.arm(m, Date.now() + (o.startedAt ? 120000 : 30000) + Math.floor(Math.random() * 5000));
      this.hooks.save(m);
      return { accepted: true };
    });
  }
  async poll(guild: string) {
    const due = this.sql
      .exec<{ match_id: string }>(
        "SELECT match_id FROM deadlines WHERE kind='lobby' AND due<=? ORDER BY due LIMIT 4",
        Date.now(),
      )
      .toArray();
    for (const { match_id } of due) {
      const candidate = this.ctx.storage.transactionSync(() => {
        const m = this.hooks.match(match_id),
          b = m.lobby;
        const job = this.sql
          .exec<{ due: number }>('SELECT due FROM deadlines WHERE key=?', `lobby:${m.id}`)
          .toArray()[0];
        if (!job || job.due > Date.now()) return;
        if (!b || !this.current(m, b.id) || complete(m)) {
          this.sql.exec('DELETE FROM deadlines WHERE key=?', `lobby:${m.id}`);
          return;
        }
        this.arm(m, Date.now() + 30000); // durable lease: recover a crash without a tight loop
        return { m, b };
      });
      if (!candidate) continue;
      const { m, b } = candidate;
      let result: ProviderResult<LobbyObservation>;
      let providerRetryAt = b.providerRetryAt ?? 0;
      try {
        const [observed, occupancy] = await Promise.all([
          providerRetryAt > Date.now()
            ? Promise.resolve({
                status: 'unavailable' as const,
                retryAfterMs: providerRetryAt - Date.now(),
              })
            : gameAdapter(b.adapterId)!.observe(b.externalId, !!b.observation?.startedAt),
          b.adapterId === 'aoe2' && !b.observation?.startedAt
            ? this.hooks.occupancy?.(b.externalId).catch(() => null)
            : null,
        ]);
        providerRetryAt =
          observed.status === 'ok'
            ? 0
            : observed.retryAfterMs
              ? Date.now() + observed.retryAfterMs
              : providerRetryAt;
        result =
          observed.status === 'ok'
            ? observed
            : occupancy
              ? { status: 'ok', value: occupancy }
              : observed;
      } catch {
        result = { status: 'unavailable' };
      }
      // Network work is complete. Recheck saved binding and roster before any write.
      if (result.status === 'ok') {
        const accepted = this.accept(
          guild,
          {
            schemaVersion: 1,
            communityId: guild,
            gameId: m.queue.gameId,
            matchId: m.id,
            bindingId: b.id,
            rosterVersion: b.rosterVersion,
            adapterId: b.adapterId,
            eventId: crypto.randomUUID(),
            observation: result.value,
          },
          providerRetryAt,
        );
        if (accepted.accepted) continue;
      }
      this.ctx.storage.transactionSync(() => {
        const current = this.hooks.match(m.id);
        if (!this.current(current, b.id)) return;
        const saved = current.lobby!;
        if (complete(current)) return;
        if (saved.state !== 'unavailable')
          this.hooks.audit('lobby_unavailable', 'connector', { bindingId: b.id }, current);
        saved.providerRetryAt = providerRetryAt;
        saved.state = 'unavailable';
        saved.failures = Math.min(saved.failures + 1, 10);
        const retry = result.status !== 'ok' ? (result.retryAfterMs ?? 0) : 0;
        this.arm(
          current,
          Date.now() +
            Math.max(retry, Math.min(900000, 30000 * 2 ** (saved.failures - 1))) +
            Math.floor(Math.random() * 5000),
        );
        this.hooks.save(current);
      });
    }
  }
}

import { observeGamePlayers } from './game-records';
import { discoverLobby, type DiscoveryTarget } from '../domain/lobby-discovery';
import { gameCapabilities } from '../games/catalog';
import type { Settings } from '../domain/config';
import type { Action, Match } from '../domain/play';
import { PlayError } from '../domain/errors';
import {
  lobbyEventSchema,
  observationSchema,
  lobbySummary,
  lobbySearchSummary,
  rosterMatches,
  normalizeObservation,
  suggestedResult,
  validObservation,
  type LobbyBinding,
  type LobbyEvent,
} from '../domain/lobby';
import { gameAdapter } from '../games/registry';
import { externalRecordingReference } from '../games/manual';
import type {
  ConnectorOperation,
  GameAdapter,
  ProviderResult,
  LobbyObservation,
} from '../games/types';

interface Hooks {
  adapter?(id: string): GameAdapter | undefined;
  availability?(adapter: GameAdapter, operation: ConnectorOperation, mode?: string): string;
  occupancy?(id: string, discoveryName?: string): Promise<LobbyObservation | null>;
  discover?(
    adapterId: string,
    name: string,
  ): Promise<{ healthy: boolean; observations: LobbyObservation[] }>;
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
const complete = (m: Match) => m.lobby?.observation?.phase === 'finished';

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
    const manifest = this.enabled(m)?.manifest;
    return m.teams.flatMap((team, side) =>
      team.map((p) => {
        const refs = this.sql
          .exec<{
            external_id: string;
            provenance: string;
            edition: string | null;
            namespace: string | null;
          }>(
            'SELECT r.external_id,r.provenance,c.edition,c.namespace FROM profile_refs r LEFT JOIN profile_claims c ON c.game_id=r.game_id AND c.provider=r.provider AND c.external_id=r.external_id AND c.user_id=r.user_id WHERE r.game_id=? AND r.provider=? AND r.user_id=? ORDER BY r.external_id',
            m.queue.gameId,
            provider,
            p.id,
          )
          .toArray()
          .filter(
            (r) =>
              !r.provenance.startsWith('inferred:') &&
              !!manifest &&
              ((r.edition === manifest.edition && r.namespace === manifest.accountNamespace) ||
                (manifest.id === 'aoe2' &&
                  provider === 'aoe2' &&
                  r.edition === null &&
                  r.namespace === null)),
          );
        if (refs.length > 1 && this.availability(m, 'lobbyObservation') === 'available')
          throw new PlayError(
            'A player has several profiles for this provider. Remove ambiguous links before attaching.',
          );
        return {
          userId: p.id,
          profileId:
            refs.length !== 1 || refs[0]?.provenance.startsWith('inferred:')
              ? null
              : (refs[0]?.external_id ?? null),
          side,
        };
      }),
    );
  }
  private enabled(m: Match) {
    const game = this.settings().games.find((g) => g.id === m.queue.gameId);
    return game?.catalogId ? this.adapter(game.catalogId) : undefined;
  }
  private adapter(id: string) {
    return this.hooks.adapter?.(id) ?? gameAdapter(id);
  }
  private availability(m: Match, operation: ConnectorOperation, mode?: string): string {
    const game = this.settings().games.find((g) => g.id === m.queue.gameId);
    const adapter = this.enabled(m);
    if (!adapter) return 'unsupported';
    const state =
      this.hooks.availability?.(adapter, operation, mode) ??
      (adapter.manifest.capabilities[operation] === 'unsupported'
        ? 'unsupported'
        : adapter.manifest.acceptance === 'legacy'
          ? 'available'
          : 'acceptance_required');
    if (state !== 'available') return state;
    if (game?.connector?.capabilities?.[operation] === false) return 'disabled';
    if (
      ['lobbyObservation', 'lobbyDiscovery', 'ongoingDiscovery'].includes(operation) &&
      !game?.connector?.enabled
    )
      return 'disabled';
    return 'available';
  }
  private searchName(m: Match): string | undefined {
    const game = this.settings().games.find((g) => g.id === m.queue.gameId);
    if (
      this.availability(m, 'lobbyDiscovery') !== 'available' ||
      m.state !== 'playing' ||
      !active(m) ||
      m.lobbyAutoDisabled ||
      Date.now() >= (m.playingAt ?? m.createdAt) + 90 * 60000 ||
      m.mapRound?.state === 'open' ||
      ['started', 'finished'].includes(m.lobby?.observation?.phase ?? '') ||
      (m.lobby && !m.lobby.automatic)
    )
      return;
    return game?.connector?.autoAttach?.lobbyName;
  }
  private syncSearch(m: Match) {
    const name = this.searchName(m);
    const until = Math.min(m.expiresAt ?? Infinity, (m.playingAt ?? m.createdAt) + 90 * 60000);
    if (!name || Date.now() >= until) {
      this.sql.exec('DELETE FROM deadlines WHERE key=?', `lobby-search:${m.id}`);
      if (m.lobbySearch) {
        m.lobbySearch.state = 'stopped';
        delete m.lobbySearch.candidate;
        delete m.lobbySearch.candidateUrl;
      }
      return;
    }
    if (!m.lobbySearch || m.lobbySearch.name !== name || m.lobbySearch.state === 'stopped')
      m.lobbySearch = { name, until, state: 'searching' };
    this.sql.exec(
      'INSERT OR IGNORE INTO deadlines VALUES(?,?,?,?)',
      `lobby-search:${m.id}`,
      'lobby_discovery',
      m.id,
      Date.now(),
    );
  }
  private discoveryTargets(m: Match): { targets: DiscoveryTarget[]; claimed: Set<string> } {
    const matches = this.sql
      .exec<{ config: string }>("SELECT config FROM matches WHERE state IN ('playing','check_in')")
      .toArray()
      .map((row) => JSON.parse(row.config) as Match)
      .filter(active);
    const adapter = this.enabled(m)!;
    const targets = matches
      .filter((other) => this.enabled(other)?.id === adapter.id && this.searchName(other))
      .map((other) => ({
        id: other.id,
        name: this.searchName(other)!,
        profiles: this.profiles(other, adapter.profileProvider),
      }));
    const claimed = new Set(
      matches
        .filter(
          (other) =>
            other.id !== m.id &&
            other.lobby?.adapterId === adapter.id &&
            ['pending', 'observed', 'unavailable'].includes(other.lobby.state),
        )
        .map((other) => other.lobby!.externalId),
    );
    return { targets, claimed };
  }
  private async pollDiscovery(guild: string) {
    const due = this.sql
      .exec<{ match_id: string }>(
        "SELECT match_id FROM deadlines WHERE kind='lobby_discovery' AND due<=? ORDER BY due LIMIT 4",
        Date.now(),
      )
      .toArray();
    for (const { match_id } of due) {
      const request = this.ctx.storage.transactionSync(() => {
        const m = this.hooks.match(match_id);
        const oldSearch = JSON.stringify(m.lobbySearch);
        this.syncSearch(m);
        const job = this.sql
          .exec<{ due: number }>('SELECT due FROM deadlines WHERE key=?', `lobby-search:${m.id}`)
          .toArray()[0];
        if (!job || job.due > Date.now()) {
          if (oldSearch !== JSON.stringify(m.lobbySearch)) this.hooks.save(m);
          return;
        }
        this.sql.exec(
          'UPDATE deadlines SET due=? WHERE key=?',
          Math.min(Date.now() + 30000, m.lobbySearch!.until),
          `lobby-search:${m.id}`,
        );
        return { m, name: this.searchName(m)!, adapter: this.enabled(m)! };
      });
      if (!request) continue;
      const { m: before, name, adapter } = request;
      let result: { healthy: boolean; observations: LobbyObservation[] };
      try {
        result = (await this.hooks.discover?.(adapter.id, name)) ?? {
          healthy: false,
          observations: [],
        };
      } catch {
        result = { healthy: false, observations: [] };
      }
      this.ctx.storage.transactionSync(() => {
        const m = this.hooks.match(before.id);
        // A player-supplied ID, roster edit, cancellation or setting change wins over an in-flight search.
        if (
          m.version !== before.version ||
          m.rosterVersion !== before.rosterVersion ||
          this.searchName(m) !== name ||
          this.enabled(m)?.id !== adapter.id ||
          !m.lobbySearch ||
          Date.now() >= m.lobbySearch.until ||
          m.lobby?.id !== before.lobby?.id
        )
          return;
        const search = m.lobbySearch;
        delete search.candidate;
        delete search.candidateUrl;
        if (!result.healthy) search.state = 'unavailable';
        else {
          try {
            const { targets, claimed } = this.discoveryTargets(m);
            const selected = discoverLobby(m.id, targets, result.observations, claimed);
            search.state = selected.state;
            search.candidate = selected.candidate;
            search.candidateUrl = selected.candidate
              ? adapter.lobbyUrl(selected.candidate.externalId)
              : undefined;
            const chosen = selected.attach;
            // A remake may replace an automatically attached, unstarted lobby only with the same players.
            const previous = m.lobby?.discoveredProfiles?.slice().sort();
            const samePlayers =
              !m.lobby ||
              m.lobby.externalId === chosen?.externalId ||
              (!!previous?.length &&
                JSON.stringify(previous) ===
                  JSON.stringify(chosen?.players.map((p) => p.profileId).sort()));
            if (
              chosen &&
              (chosen.externalId !== m.lobby?.externalId ||
                ['invalidated', 'stopped'].includes(m.lobby!.state)) &&
              samePlayers
            ) {
              this.bind(m, adapter, chosen.externalId, 'connector', true, chosen);
              this.accept(guild, {
                schemaVersion: 2,
                communityId: guild,
                gameId: m.queue.gameId,
                matchId: m.id,
                bindingId: m.lobby!.id,
                rosterVersion: m.rosterVersion ?? 0,
                adapterId: adapter.id,
                eventId: crypto.randomUUID(),
                observation: chosen,
              });
              return;
            }
          } catch (error) {
            if (!(error instanceof PlayError)) throw error;
            search.state = 'ambiguous';
          }
        }
        this.hooks.save(m);
      });
    }
  }
  action(m: Match, a: Action) {
    if (a.kind === 'lobby_status')
      return {
        ok: true,
        content:
          !m.lobby && m.lobbySearch ? lobbySearchSummary(m.lobbySearch) : lobbySummary(m.lobby),
      };
    if (!['check_in', 'playing'].includes(m.state) || !active(m))
      throw new PlayError('This match is closed.');
    if (!a.admin && !m.roster.some((p) => p.id === a.actor.id))
      throw new PlayError('Only a player in this match or an administrator can attach a lobby.');
    if (a.kind === 'lobby_unlink') {
      if (!a.admin && !m.captains.includes(a.actor.id))
        throw new PlayError('Only a captain or administrator can unlink a lobby.');
      m.lobbyAutoDisabled = true;
      if (m.lobby) {
        m.lobby.state = 'unlinked';
        m.lobby.reason = 'Unlinked by a captain or administrator.';
      }
      this.hooks.save(m);
      this.hooks.audit('lobby_unlinked', a.actor.id, {}, m);
      return { ok: true, content: 'Lobby unlinked. Manual reporting remains available.' };
    }
    if (a.kind === 'lobby_confirm') {
      if (!a.admin && !m.captains.includes(a.actor.id))
        throw new PlayError('Only a captain or administrator can confirm the lobby.');
      const b = m.lobby;
      if (!b || b.id !== a.reference || !this.current(m, b.id))
        throw new PlayError('Check /lobby status and confirm its current binding ID.');
      if (b.adapterId === 'wc3' && b.observation && !rosterMatches(b, b.observation))
        throw new PlayError(
          'W3Champions participants and teams must match this bot match before confirmation.',
        );
      b.confirmedBy = a.actor.id;
      b.confirmedAt = Date.now();
      if (b.observation) {
        b.observation.confidence = 'confirmed';
        if (b.observation.startedAt) this.hooks.started?.(m, b.observation, `confirm:${b.id}`);
        observeGamePlayers(this.sql, m, this.adapter(b.adapterId)!.profileProvider, b.observation);
      }
      this.hooks.save(m);
      this.hooks.audit('lobby_confirmed', a.actor.id, { bindingId: b.id }, m);
      return { ok: true, content: lobbySummary(b) };
    }
    if (a.kind === 'lobby_facts') {
      const b = m.lobby;
      if (!b || !this.current(m, b.id)) throw new PlayError('Attach the current lobby first.');
      if (!a.facts || !Object.keys(a.facts).length)
        throw new PlayError('Provide a faction, map, duration or external recording reference.');
      const f = a.facts;
      if (
        (f.map !== undefined || f.elapsedSeconds !== undefined || f.recordingUrl) &&
        !a.admin &&
        !m.captains.includes(a.actor.id)
      )
        throw new PlayError('Only a captain or administrator can declare shared match facts.');
      const facts: NonNullable<LobbyBinding['manualFacts']> = b.manualFacts ?? {
        players: [],
        declaredBy: a.actor.id,
        declaredAt: Date.now(),
      };
      if (f.selection) {
        const side = m.teams.findIndex((team) => team.some((p) => p.id === a.actor.id));
        if (side < 0) throw new PlayError('Only a participant can declare their faction.');
        facts.players = facts.players.filter((p) => p.userId !== a.actor.id);
        facts.players.push({ userId: a.actor.id, side, selection: f.selection });
      }
      if (f.map !== undefined) facts.map = f.map;
      if (f.elapsedSeconds !== undefined) facts.elapsedSeconds = f.elapsedSeconds;
      if (f.recordingUrl) {
        const reference = externalRecordingReference(f.recordingUrl, m.id);
        facts.recordingLinks = [
          ...new Set([...(facts.recordingLinks ?? []), reference.sourceUrl!]),
        ].slice(-8);
      }
      facts.declaredBy = a.actor.id;
      facts.declaredAt = Date.now();
      b.manualFacts = facts;
      this.hooks.save(m);
      this.hooks.audit(
        'lobby_facts_declared',
        a.actor.id,
        { bindingId: b.id, fields: Object.keys(f) },
        m,
      );
      return { ok: true, content: lobbySummary(b) };
    }
    const adapter = this.enabled(m);
    if (!adapter)
      throw new PlayError(
        'Choose a supported catalog game in settings. Manual result reporting remains available.',
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
    ) {
      // An explicit choice pins even an already auto-discovered lobby.
      if (m.lobby.automatic)
        this.hooks.audit('lobby_pinned', a.actor.id, { bindingId: m.lobby.id, externalId }, m);
      m.lobby.automatic = false;
      m.lobbyAutoDisabled = true;
      this.hooks.save(m);
      return { ok: true, content: lobbySummary(m.lobby) };
    }
    this.bind(m, adapter, externalId, a.actor.id, false);
    return { ok: true, content: lobbySummary(m.lobby) };
  }
  private bind(
    m: Match,
    adapter: GameAdapter,
    externalId: string,
    actor: string,
    automatic: boolean,
    discovered?: LobbyObservation,
  ) {
    const others = this.sql
      .exec<{ config: string }>(
        "SELECT config FROM matches WHERE state IN ('playing','check_in') AND id!=?",
        m.id,
      )
      .toArray();
    if (
      others.some((row) => {
        const other: Match = JSON.parse(row.config);
        return (
          active(other) &&
          other.lobby?.adapterId === adapter.id &&
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
      referenceKind:
        adapter.id === 'wc3' || externalId.startsWith('aoe4world:') ? 'match' : 'lobby',
      url: adapter.lobbyUrl(externalId),
      attachedAt: Date.now(),
      rosterVersion: m.rosterVersion ?? 0,
      profiles: this.profiles(m, adapter.profileProvider),
      state: 'pending',
      failures: 0,
      rosterVerified: false,
      prompted: false,
      automatic,
      availability: this.availability(m, 'lobbyObservation'),
      ...(discovered ? { discoveredProfiles: discovered.players.map((p) => p.profileId) } : {}),
      occupancyAvailable: gameCapabilities(adapter.id)?.occupancy ?? false,
    };
    if (!automatic) m.lobbyAutoDisabled = true;
    if (this.availability(m, 'lobbyObservation') === 'available') this.arm(m, Date.now());
    this.hooks.save(m);
    this.hooks.audit(
      'lobby_attached',
      actor,
      { bindingId: m.lobby.id, externalId, adapterId: adapter.id, automatic },
      m,
    );
  }
  /** Called with every match save, including cancellation, reporting and roster changes. */
  sync(m: Match) {
    this.syncSearch(m);
    const b = m.lobby;
    if (!b) return;
    if (!active(m) && !['unlinked', 'invalidated'].includes(b.state)) {
      b.state = 'stopped';
      b.reason = 'Match closed or lifetime elapsed.';
    } else if (b.rosterVersion !== (m.rosterVersion ?? 0)) {
      b.state = 'invalidated';
      b.reason = 'Roster changed; explicitly attach the lobby again.';
    }
    b.availability = this.availability(m, 'lobbyObservation');
    if (
      ['unlinked', 'invalidated', 'stopped'].includes(b.state) ||
      complete(m) ||
      b.availability !== 'available'
    )
      this.sql.exec('DELETE FROM deadlines WHERE key=?', `lobby:${m.id}`);
    else if (
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
      this.hooks.save(m);
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
        inputObservation = event.observation;
      if (this.availability(m, 'lobbyObservation', inputObservation.mode) !== 'available')
        return { accepted: false, reason: 'capability_unavailable' };
      if (
        b.adapterId === 'wc3' &&
        (!inputObservation.mode ||
          !this.adapter(b.adapterId)?.manifest.supportedModes?.includes(inputObservation.mode))
      )
        return { accepted: false, reason: 'unsupported_mode' };
      if (!validObservation(inputObservation, Date.now()))
        return { accepted: false, reason: 'invalid_evidence' };
      if (inputObservation.bindingGeneration && inputObservation.bindingGeneration !== b.id)
        return { accepted: false, reason: 'invalid_binding' };
      const o = normalizeObservation(inputObservation, b.id);
      if (
        event.adapterId !== b.adapterId ||
        event.rosterVersion !== b.rosterVersion ||
        (o.externalId !== b.externalId &&
          !(
            b.adapterId === 'wc3' &&
            b.externalId.startsWith('record:') &&
            o.providerIds?.recordId === b.externalId.slice(7)
          )) ||
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
      const phaseOrder = { open: 0, full: 0, started: 1, finished: 2 };
      if (
        b.observation &&
        (phaseOrder[o.phase] < phaseOrder[b.observation.phase] ||
          (b.observation.nativeMatchId &&
            o.nativeMatchId &&
            b.observation.nativeMatchId !== o.nativeMatchId) ||
          (b.observation.nativeLobbyId &&
            o.nativeLobbyId &&
            b.observation.nativeLobbyId !== o.nativeLobbyId))
      )
        return { accepted: false, reason: 'conflicting_evidence' };
      const duplicate = this.sql
        .exec<{ config: string }>(
          "SELECT config FROM matches WHERE id!=? AND state IN ('check_in','playing','finished')",
          m.id,
        )
        .toArray()
        .some((row) => {
          const other = JSON.parse(row.config) as Match;
          const bound = other.lobby;
          return (
            bound?.adapterId === b.adapterId &&
            (other.state === 'finished' ||
              ['pending', 'observed', 'unavailable'].includes(bound.state)) &&
            (((b.adapterId === 'wc3' || b.referenceKind === 'match') &&
              bound.externalId === b.externalId) ||
              (!!o.nativeMatchId && bound.observation?.nativeMatchId === o.nativeMatchId) ||
              (!!o.providerIds?.recordId &&
                bound.externalId === `record:${o.providerIds.recordId}`))
          );
        });
      if (duplicate) return { accepted: false, reason: 'already_bound' };
      const changed = b.observation?.phase !== o.phase || b.state !== 'observed';
      if (providerRetryAt !== undefined) b.providerRetryAt = providerRetryAt;
      b.state = 'observed';
      b.observation = o;
      b.lastSuccessAt = o.observedAt;
      b.lastEventId = event.eventId;
      b.failures = 0;
      b.rosterVerified = rosterMatches(b, o);
      o.confidence = b.confirmedBy ? 'confirmed' : b.rosterVerified ? 'roster' : 'provisional';
      b.suggestedResult = suggestedResult(b, o);
      const inferred = observeGamePlayers(
        this.sql,
        m,
        this.adapter(b.adapterId)!.profileProvider,
        o,
      );
      b.identitySuggestions = inferred;
      if (o.startedAt && (b.rosterVerified || (b.confirmedBy && b.adapterId !== 'wc3')))
        this.hooks.started?.(m, o, event.eventId);
      if (
        o.phase === 'finished' &&
        b.rosterVerified &&
        b.suggestedResult &&
        m.state === 'playing' &&
        !b.prompted
      ) {
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
        this.arm(
          m,
          Date.now() +
            (['started', 'finished'].includes(o.phase) ? 120000 : 30000) +
            Math.floor(Math.random() * 5000),
        );
      this.hooks.save(m);
      return { accepted: true };
    });
  }
  async refreshReplays(m: Match, a: Action) {
    if (!a.admin && !m.roster.some((p) => p.id === a.actor.id))
      throw new PlayError('Only a participant or administrator can refresh recording references.');
    const before = m.lobby;
    if (!before || ['unlinked', 'invalidated'].includes(before.state))
      throw new PlayError('Attach a valid lobby first.');
    const adapter = this.enabled(m);
    if (!adapter?.replays || this.availability(m, 'replayReferences') !== 'available')
      return {
        ok: false,
        content:
          'Provider recording references are unavailable. External recording links can be declared with /lobby facts.',
      };
    const matchId =
      before.observation?.replayReferences?.[0]?.matchId ?? before.observation?.nativeMatchId;
    if (!matchId)
      return {
        ok: false,
        content: 'A native match or replay identifier has not been established yet.',
      };
    const result = await adapter.replays(matchId);
    if (result.status !== 'ok')
      return {
        ok: false,
        content: `Recording metadata: ${result.status}. No recording has been downloaded or archived.`,
      };
    const references = observationSchema.shape.replayReferences.parse(result.value)!;
    return this.ctx.storage.transactionSync(() => {
      const current = this.hooks.match(m.id);
      const b = current.lobby;
      if (
        !b ||
        b.id !== before.id ||
        b.rosterVersion !== before.rosterVersion ||
        ['unlinked', 'invalidated'].includes(b.state) ||
        this.enabled(current)?.id !== adapter.id ||
        this.availability(current, 'replayReferences') !== 'available' ||
        !b.observation
      )
        return {
          ok: false,
          content: 'The lobby binding changed. Refresh its current status first.',
        };
      b.observation.replayReferences = references;
      this.hooks.save(current);
      return {
        ok: true,
        content: references.length
          ? references
              .map(
                (r) =>
                  `${r.provider}: ${r.expiresAt && r.expiresAt <= Date.now() ? 'expired' : r.availability}${r.sourceUrl && r.availability === 'available' && (!r.expiresAt || r.expiresAt > Date.now()) ? ` — ${r.sourceUrl}` : ''}`,
              )
              .join('\n')
          : 'No provider recording references are available.',
      };
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
        if (
          !b ||
          !this.current(m, b.id) ||
          complete(m) ||
          this.availability(m, 'lobbyObservation') !== 'available'
        ) {
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
            : this.adapter(b.adapterId)!.observe(
                b.externalId,
                ['started', 'finished'].includes(b.observation?.phase ?? ''),
                {
                  matchCreatedAt: m.createdAt,
                  attachedAt: b.attachedAt,
                  profileIds: b.profiles.flatMap((p) => (p.profileId ? [p.profileId] : [])),
                  nativeMatchId: b.observation?.nativeMatchId,
                  previous: b.observation,
                },
              ),
          b.adapterId === 'aoe2' && !b.observation?.startedAt
            ? this.hooks
                .occupancy?.(b.externalId, b.automatic ? m.lobbySearch?.name : undefined)
                .catch(() => null)
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
            schemaVersion: 2,
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
        saved.failure = result.status === 'ok' ? 'invalid_response' : result.status;
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
    await this.pollDiscovery(guild);
  }
}

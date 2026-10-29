import type { LobbyObservation, LobbyPlayer } from './types';
import { normalizeLobbyName } from '../domain/lobby';
import { parseAoE2Match } from './aoe2';
import { gameCapabilities } from './catalog';
type Row = Record<string, unknown>;
type Lobby = { data: Row; slots: Map<number, Row>; addedAt: number };
const idOf = (x: unknown) => (/^[1-9]\d{0,11}$/.test(String(x)) ? String(x) : null);
export const aoe2OngoingFeedUrl =
  'https://socket.aoe2companion.com/listen?handler=ongoing-matches&verified=true';
export const aoe2DiscoveryLimit = 200;

/** The initial ongoing feed is one complete array of matchAdded events, not lobby deltas. */
export function parseAoE2OngoingSnapshot(input: unknown, now = Date.now()): LobbyObservation[] {
  if (!Array.isArray(input) || input.length > 1000) throw Error('Invalid ongoing snapshot');
  const ids = new Set<string>();
  const observations: LobbyObservation[] = [];
  for (const raw of input) {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw Error('Invalid ongoing event');
    const event = raw as Row;
    if (
      event.type !== 'matchAdded' ||
      !event.data ||
      typeof event.data !== 'object' ||
      Array.isArray(event.data)
    )
      throw Error('Incomplete ongoing baseline');
    const match = event.data as Row,
      id = idOf(match.matchId);
    if (!id || ids.has(id)) throw Error('Invalid ongoing match identity');
    ids.add(id);
    // This feed has flat players; HTTP match lookup has teams. Preserve native player teams.
    const value = parseAoE2Match(
      Array.isArray(match.teams) ? match : { ...match, teams: [{ players: match.players }] },
      id,
      now,
    );
    if (!value.startedAt || !value.players.length) throw Error('Invalid ongoing match');
    if (value.finishedAt || match.privacy === 'private') continue;
    observations.push({
      ...value,
      referenceKind: 'match',
      source: 'aoe2companion-ongoing-matches',
      visibility: match.privacy === 'public' ? 'public' : 'unknown',
      ...(typeof match.patch === 'string' || typeof match.patch === 'number'
        ? { gameBuild: String(match.patch).slice(0, 100) }
        : {}),
      ...(typeof match.gameMode === 'string' ? { mode: match.gameMode.slice(0, 100) } : {}),
      elapsedDuration: { value: (now - value.startedAt) / 1000, unit: 'seconds' },
    });
  }
  return observations
    .sort((a, b) => b.startedAt! - a.startedAt! || a.externalId.localeCompare(b.externalId))
    .slice(0, aoe2DiscoveryLimit);
}
/** A connection-local snapshot. Clearing on disconnect prevents stale occupancy after reconnect. */
export class AoE2LobbySnapshot {
  private lobbies = new Map<string, Lobby>();
  private firstFrameAt = 0;
  lastFrameAt = 0;
  has(id: string) {
    return this.lobbies.has(id);
  }
  named(name: string, now = Date.now()): LobbyObservation[] {
    return [...this.lobbies.keys()]
      .map((id) => this.read(id, now))
      .filter(
        (o): o is LobbyObservation =>
          !!o && normalizeLobbyName(o.name) === normalizeLobbyName(name),
      );
  }
  browse(now = Date.now()): LobbyObservation[] | null {
    if (!this.firstFrameAt || now - this.firstFrameAt < 2000 || now - this.lastFrameAt > 90000)
      return null;
    return [...this.lobbies.keys()]
      .sort()
      .map((id) => this.read(id, now))
      .filter((value): value is LobbyObservation => value !== null)
      .slice(0, aoe2DiscoveryLimit);
  }
  clear() {
    this.lobbies.clear();
    this.firstFrameAt = 0;
    this.lastFrameAt = 0;
  }
  consume(input: unknown, tracked: Set<string>, now = Date.now()) {
    const events = Array.isArray(input) ? input : [input];
    const browsing = tracked.has('browse:*');
    if (events.length > 10000) throw Error('Too many feed events');
    if (
      browsing &&
      ((!this.firstFrameAt && !Array.isArray(input)) ||
        events.some((event) => {
          if (!event || typeof event !== 'object' || Array.isArray(event)) return true;
          const value = event as Row;
          return (
            ![
              'lobbyAdded',
              'lobbyUpdated',
              'lobbyRemoved',
              'slotAdded',
              'slotUpdated',
              'slotRemoved',
            ].includes(String(value.type)) ||
            !value.data ||
            typeof value.data !== 'object' ||
            Array.isArray(value.data) ||
            !idOf((value.data as Row).matchId)
          );
        }))
    )
      throw Error('Invalid lobby browse baseline');
    for (const raw of events) {
      if (!raw || typeof raw !== 'object') continue;
      const event = raw as { type?: string; data?: Row },
        d = event.data;
      if (!d || typeof d !== 'object') continue;
      const id = idOf(d.matchId);
      if (!id) continue;
      const previous = this.lobbies.get(id);
      const lobbyName = ['lobbyAdded', 'lobbyUpdated'].includes(event.type ?? '')
        ? (d.name ?? previous?.data.name)
        : previous?.data.name;
      const named = tracked.has(`name:${normalizeLobbyName(String(lobbyName ?? ''))}`);
      if (!browsing && !tracked.has(id) && !named) {
        this.lobbies.delete(id);
        continue;
      }
      if (event.type === 'lobbyRemoved') {
        this.lobbies.delete(id);
        continue;
      }
      if (event.type === 'lobbyAdded') {
        if (!this.lobbies.has(id) && this.lobbies.size >= 1000)
          throw Error('Too many matching lobbies');
        this.lobbies.set(id, { data: { ...d }, slots: new Map(), addedAt: now });
        continue;
      }
      const lobby = this.lobbies.get(id);
      if (!lobby) continue;
      if (event.type === 'lobbyUpdated') {
        Object.assign(lobby.data, d);
        continue;
      }
      const slot = Number(d.slot);
      if (!Number.isInteger(slot) || slot < 0 || slot > 15) continue;
      if (event.type === 'slotRemoved') lobby.slots.delete(slot);
      if (event.type === 'slotAdded') lobby.slots.set(slot, { ...d });
      if (event.type === 'slotUpdated' && lobby.slots.has(slot))
        Object.assign(lobby.slots.get(slot)!, d);
    }
    for (const [id, lobby] of this.lobbies)
      if (
        !browsing &&
        !tracked.has(id) &&
        !tracked.has(`name:${normalizeLobbyName(String(lobby.data.name ?? ''))}`)
      )
        this.lobbies.delete(id);
    this.firstFrameAt ||= now;
    this.lastFrameAt = now;
  }
  read(id: string, now = Date.now()): LobbyObservation | null {
    const lobby = this.lobbies.get(id);
    if (!lobby || !this.lastFrameAt || now - this.lastFrameAt > 90000 || now - lobby.addedAt < 2000)
      return null;
    const total = Number(lobby.data.totalSlotCount),
      blocked = Number(lobby.data.blockedSlotCount);
    const capacity =
      Number.isInteger(total) &&
      Number.isInteger(blocked) &&
      total > blocked &&
      total <= 16 &&
      blocked >= 0
        ? total - blocked
        : null;
    const players: LobbyPlayer[] = [];
    for (const row of lobby.slots.values()) {
      const id = idOf(row.profileId);
      if (id)
        players.push({
          profileId: id,
          name: typeof row.name === 'string' ? row.name.slice(0, 100) : `Player ${id}`,
          team: row.team == null ? null : String(row.team),
          ...(typeof row.civName === 'string' && row.civName
            ? { selection: row.civName.slice(0, 100) }
            : {}),
          ...(typeof row.civ === 'string' || typeof row.civ === 'number'
            ? { selectionId: String(row.civ).slice(0, 100) }
            : {}),
        });
    }
    if (new Set(players.map((p) => p.profileId)).size !== players.length) return null;
    return {
      externalId: id,
      schemaVersion: 2,
      referenceKind: 'lobby',
      nativeLobbyId: id,
      edition: gameCapabilities('aoe2')!.manifest.edition,
      provider: 'aoe2companion',
      accountNamespace: 'aoe2:profile',
      confidence: 'provisional',
      name: String(lobby.data.name ?? 'AoE2 lobby').slice(0, 100),
      phase: capacity !== null && players.length === capacity ? 'full' : 'open',
      observedAt: now,
      startedAt: null,
      finishedAt: null,
      players,
      capacity,
      map: typeof lobby.data.mapName === 'string' ? lobby.data.mapName.slice(0, 100) : null,
      source: 'aoe2companion-lobbies',
    };
  }
}

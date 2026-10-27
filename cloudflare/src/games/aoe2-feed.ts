import type { LobbyObservation, LobbyPlayer } from './types';
type Row = Record<string, unknown>;
type Lobby = { data: Row; slots: Map<number, Row>; addedAt: number };
const idOf = (x: unknown) => (/^[1-9]\d{0,11}$/.test(String(x)) ? String(x) : null);
/** A connection-local snapshot. Clearing on disconnect prevents stale occupancy after reconnect. */
export class AoE2LobbySnapshot {
  private lobbies = new Map<string, Lobby>();
  lastFrameAt = 0;
  clear() {
    this.lobbies.clear();
    this.lastFrameAt = 0;
  }
  consume(input: unknown, tracked: Set<string>, now = Date.now()) {
    const events = Array.isArray(input) ? input : [input];
    if (events.length > 10000) throw Error('Too many feed events');
    for (const raw of events) {
      if (!raw || typeof raw !== 'object') continue;
      const event = raw as { type?: string; data?: Row },
        d = event.data;
      if (!d || typeof d !== 'object') continue;
      const id = idOf(d.matchId);
      if (!id || !tracked.has(id)) continue;
      if (event.type === 'lobbyRemoved') {
        this.lobbies.delete(id);
        continue;
      }
      if (event.type === 'lobbyAdded') {
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
    for (const id of this.lobbies.keys()) if (!tracked.has(id)) this.lobbies.delete(id);
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
        });
    }
    if (new Set(players.map((p) => p.profileId)).size !== players.length) return null;
    return {
      externalId: id,
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

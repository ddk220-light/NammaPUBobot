import { normalizeLobbyName, validObservation, type LobbyBinding } from './lobby';
import type { LobbyObservation } from '../games/types';

export interface DiscoveryTarget {
  id: string;
  name: string;
  profiles: LobbyBinding['profiles'];
}
/** The legacy name/full/count rule, with declared-identity and concurrent-match checks. */
function score(target: DiscoveryTarget, o: LobbyObservation): number | null {
  if (
    normalizeLobbyName(o.name) !== normalizeLobbyName(target.name) ||
    o.capacity !== target.profiles.length ||
    !o.players.length ||
    o.players.length > o.capacity ||
    !['open', 'full'].includes(o.phase)
  )
    return null;
  const full = o.players.length === o.capacity;
  const sides = [new Set<string>(), new Set<string>()];
  let found = 0;
  for (const p of target.profiles) {
    if (!p.profileId) continue;
    const player = o.players.find((r) => r.profileId === p.profileId);
    if (!player) {
      if (full) return null;
      else continue;
    }
    found++;
    if (player.team && !['0', '-1', '-2'].includes(player.team)) sides[p.side].add(player.team);
  }
  if (sides.some((s) => s.size > 1) || [...sides[0]].some((t) => sides[1].has(t))) return null;
  return found;
}

export function discoverLobby(
  targetId: string,
  targets: DiscoveryTarget[],
  observations: LobbyObservation[],
  claimed: Set<string>,
  now = Date.now(),
): { state: 'searching' | 'ambiguous'; candidate?: LobbyObservation; attach?: LobbyObservation } {
  const candidates: LobbyObservation[] = [];
  let ambiguous = false;
  for (const o of observations) {
    if (claimed.has(o.externalId) || !validObservation(o, now) || now - o.observedAt > 90000)
      continue;
    const ranked = targets
      .map((t) => ({ id: t.id, score: score(t, o) }))
      .filter((t) => t.score !== null);
    const best = Math.max(-1, ...ranked.map((t) => t.score!));
    const winners = ranked.filter((t) => t.score === best);
    if (!winners.some((t) => t.id === targetId)) continue;
    if (winners.length !== 1) {
      ambiguous = true;
      continue;
    }
    candidates.push(o);
  }
  // Never use provider ordering or a random tie to bind a game to the wrong match.
  if (candidates.length > 1 || ambiguous) return { state: 'ambiguous' };
  const candidate = candidates[0];
  return {
    state: 'searching',
    candidate,
    attach:
      candidate?.phase === 'full' && candidate.players.length === candidate.capacity
        ? candidate
        : undefined,
  };
}

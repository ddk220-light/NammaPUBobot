import type { LobbyObservation } from '../games/types';
export interface IdentityPlayer {
  userId: string;
  name: string;
  side: number;
  profileId: string | null;
  provenance: string;
}
export interface InferredLink {
  userId: string;
  profileId: string;
  method: 'name' | 'team';
  confidence: number;
}
const normalized = (s: string) =>
  s
    .normalize('NFKC')
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]/gu, '');
export function nameSimilarity(a: string, b: string) {
  a = normalized(a);
  b = normalized(b);
  if (Math.min(a.length, b.length) < 4) return 0;
  if (a === b) return 1;
  if (Math.min(a.length, b.length) < 8) return 0;
  let prev = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i++) {
    const next = [i];
    for (let j = 1; j <= b.length; j++)
      next[j] = Math.min(
        next[j - 1] + 1,
        prev[j] + 1,
        prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1),
      );
    prev = next;
  }
  return 1 - prev[b.length] / Math.max(a.length, b.length);
}
/** Never use guesses as anchors: guests, ambiguous names and conflicting teams stay unlinked. */
export function inferLinks(roster: IdentityPlayer[], o: LobbyObservation): InferredLink[] {
  if (
    !o.startedAt ||
    o.players.length !== roster.length ||
    new Set(roster.filter((p) => p.profileId).map((p) => p.profileId)).size !==
      roster.filter((p) => p.profileId).length
  )
    return [];
  const sideTeams = [new Set<string>(), new Set<string>()];
  for (const p of roster.filter((p) => p.profileId)) {
    const observed = o.players.find((x) => x.profileId === p.profileId);
    if (!observed?.team || ['0', '-1', '-2'].includes(observed.team)) return [];
    if (!p.provenance.startsWith('inferred:')) sideTeams[p.side].add(observed.team);
  }
  if (
    sideTeams.some((s) => s.size > 1) ||
    (sideTeams[0].size && [...sideTeams[0]][0] === [...sideTeams[1]][0])
  )
    return [];
  const assigned = new Set(roster.flatMap((p) => (p.profileId ? [p.profileId] : [])));
  const unknown = roster.filter((p) => !p.profileId);
  const result: InferredLink[] = [];
  for (const p of unknown) {
    const team = [...sideTeams[p.side]][0];
    const candidates = o.players
      .filter((x) => !assigned.has(x.profileId) && (!team || x.team === team))
      .map((x) => ({ x, score: nameSimilarity(p.name, x.name) }))
      .sort((a, b) => b.score - a.score);
    const best = candidates[0];
    if (!best || best.score < 0.9 || best.score - (candidates[1]?.score ?? 0) < 0.15) continue;
    const rivals = unknown
      .filter((q) => q.userId !== p.userId)
      .map((q) => nameSimilarity(q.name, best.x.name));
    if (rivals.some((s) => best.score - s < 0.15)) continue;
    result.push({
      userId: p.userId,
      profileId: best.x.profileId,
      method: 'name',
      confidence: best.score,
    });
  }
  // A full team with three independently declared anchors permits the one remaining identity.
  // Name guesses above deliberately cannot manufacture additional anchors.
  for (const side of [0, 1]) {
    const team = [...sideTeams[side]][0],
      members = roster.filter((p) => p.side === side);
    const missing = members.filter((p) => !p.profileId);
    const trusted = members.filter((p) => p.profileId && !p.provenance.startsWith('inferred:'));
    const observed = o.players.filter((p) => p.team === team);
    if (!team || missing.length !== 1 || trusted.length < 3 || observed.length !== members.length)
      continue;
    const remaining = observed.filter((p) => !assigned.has(p.profileId));
    if (remaining.length !== 1) continue;
    if (
      result.some((r) => r.userId === missing[0].userId || r.profileId === remaining[0].profileId)
    )
      continue;
    result.push({
      userId: missing[0].userId,
      profileId: remaining[0].profileId,
      method: 'team',
      confidence: 0.95,
    });
  }
  return result;
}

import { z } from 'zod';
import type { LobbyObservation } from '../games/types';

const identifier = z.string().min(1).max(100);
export const observationSchema = z
  .object({
    externalId: identifier,
    name: z.string().max(100),
    phase: z.enum(['open', 'full', 'started', 'finished']),
    observedAt: z.number().int().nonnegative(),
    startedAt: z.number().int().nonnegative().nullable(),
    finishedAt: z.number().int().nonnegative().nullable(),
    players: z
      .array(
        z
          .object({
            profileId: identifier,
            name: z.string().max(100),
            team: identifier.nullable(),
            selection: z.string().min(1).max(100).optional(),
          })
          .strict(),
      )
      .max(16),
    capacity: z.number().int().min(1).max(16).nullable(),
    map: z.string().max(100).nullable(),
    source: identifier,
  })
  .strict();
export const lobbyEventSchema = z
  .object({
    schemaVersion: z.literal(1),
    communityId: z.string().regex(/^\d{17,20}$/),
    gameId: z.uuid(),
    matchId: z.uuid(),
    bindingId: z.uuid(),
    rosterVersion: z.number().int().nonnegative(),
    adapterId: identifier,
    eventId: identifier,
    observation: observationSchema,
  })
  .strict();
export type LobbyEvent = z.infer<typeof lobbyEventSchema>;
export interface LobbySearch {
  name: string;
  until: number;
  state: 'searching' | 'ambiguous' | 'unavailable' | 'stopped';
  candidate?: LobbyObservation;
  candidateUrl?: string;
}
export interface LobbyBinding {
  id: string;
  adapterId: string;
  externalId: string;
  url: string;
  attachedAt: number;
  rosterVersion: number;
  profiles: { userId: string; profileId: string | null; side: number }[];
  state: 'pending' | 'observed' | 'unavailable' | 'invalidated' | 'unlinked' | 'stopped';
  reason?: string;
  observation?: LobbyObservation;
  lastEventId?: string;
  providerRetryAt?: number;
  lastSuccessAt?: number;
  failures: number;
  rosterVerified: boolean;
  occupancyAvailable: boolean;
  prompted: boolean;
  automatic?: boolean;
  discoveredProfiles?: string[];
}

export const normalizeLobbyName = (name: string) => name.trim().toLocaleLowerCase('en-US');
export function lobbySearchSummary(search: LobbySearch): string {
  const o = search.candidate;
  return [
    search.state === 'stopped'
      ? 'Automatic lobby search stopped.'
      : search.state === 'unavailable'
        ? 'Lobby search temporarily unavailable; retrying.'
        : search.state === 'ambiguous'
          ? 'Several lobbies or matches could fit; waiting for an unambiguous match.'
          : `Looking for a public lobby named **${search.name}**.`,
    o
      ? `Lobby ${o.externalId}: ${o.players.length}/${o.capacity ?? '?'} slots occupied.\n${search.candidateUrl ?? ''}`
      : '',
    'Attaches when full with the expected player count. Any match player can also use /lobby attach. Results remain captain-reported.',
  ]
    .filter(Boolean)
    .join('\n');
}

/** Require positive, internally consistent evidence; absence never advances a game. */
export function validObservation(value: unknown, now: number): value is LobbyObservation {
  const parsed = observationSchema.safeParse(value);
  if (!parsed.success) return false;
  const o = parsed.data;
  if (o.observedAt > now + 30000 || now - o.observedAt > 300000) return false;
  if (new Set(o.players.map((p) => p.profileId)).size !== o.players.length) return false;
  if (
    o.startedAt !== null &&
    (o.startedAt < 946684800000 || o.startedAt > o.observedAt || o.startedAt > now)
  )
    return false;
  if (
    o.finishedAt !== null &&
    (o.startedAt === null ||
      o.finishedAt < o.startedAt ||
      o.finishedAt > o.observedAt ||
      o.finishedAt > now)
  )
    return false;
  if (o.phase === 'finished') return o.startedAt !== null && o.finishedAt !== null;
  if (o.phase === 'started') return o.startedAt !== null && o.finishedAt === null;
  return o.startedAt === null && o.finishedAt === null;
}

/** Public profile existence is not ownership proof; compare all declared identities and both teams. */
export function rosterMatches(binding: LobbyBinding, o: LobbyObservation): boolean {
  if (
    !binding.profiles.length ||
    binding.profiles.some((p) => !p.profileId) ||
    o.players.length !== binding.profiles.length
  )
    return false;
  const sides = [new Set<string>(), new Set<string>()];
  for (const p of binding.profiles) {
    const found = o.players.find((r) => r.profileId === p.profileId);
    if (!found?.team || ['-1', '-2', '0'].includes(found.team)) return false;
    sides[p.side].add(found.team);
  }
  return sides.every((s) => s.size === 1) && [...sides[0]][0] !== [...sides[1]][0];
}

export function lobbySummary(binding?: LobbyBinding, now = Date.now()): string {
  if (!binding) return 'No lobby attached. Any match player can use /lobby attach.';
  if (['invalidated', 'unlinked', 'stopped'].includes(binding.state))
    return `Lobby tracking ${binding.state}: ${binding.reason ?? 'Use /lobby attach to bind the current roster.'}`;
  const o = binding.observation;
  const stale = !o?.finishedAt && (!binding.lastSuccessAt || now - binding.lastSuccessAt > 180000);
  return [
    `Lobby ${binding.externalId}: ${binding.state === 'unavailable' ? 'unavailable' : (o?.phase ?? 'awaiting evidence')}${stale ? ' · status stale or not yet observed' : ''}.`,
    binding.url,
    binding.occupancyAvailable && o && !stale && ['open', 'full'].includes(o.phase)
      ? `${o.players.length}/${o.capacity ?? '?'} lobby slots occupied.\nJoined: ${
          binding.profiles
            .filter((p) => p.profileId && o.players.some((r) => r.profileId === p.profileId))
            .map((p) => `<@${p.userId}>`)
            .join(', ') || 'No linked players observed'
        }.\nMissing: ${
          binding.profiles
            .filter((p) => p.profileId && !o.players.some((r) => r.profileId === p.profileId))
            .map((p) => `<@${p.userId}>`)
            .join(', ') || 'None among linked players'
        }; ${binding.profiles.filter((p) => !p.profileId).length} unlinked.`
      : 'Live occupancy unavailable. Lobby disappearance is not start evidence.',
    binding.rosterVerified
      ? 'Declared profiles and teams match the game.'
      : `${binding.profiles.filter((p) => p.profileId).length}/${binding.profiles.length} players linked. Unlinked players can keep playing; /lobby link adds a profile.`,
    'Results remain manual: losing captain uses /report loss.',
  ].join('\n');
}

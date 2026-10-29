import { z } from 'zod';
import type { LobbyObservation } from '../games/types';

const identifier = z.string().min(1).max(100);
export const observationSchema = z
  .object({
    externalId: identifier,
    referenceKind: z.enum(['lobby', 'match']).optional(),
    providerIds: z.record(identifier, identifier).optional(),
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
            selectionId: identifier.optional(),
            outcome: z.enum(['win', 'loss', 'draw', 'unknown']).optional(),
            accountNamespace: identifier.optional(),
          })
          .strict(),
      )
      .max(16),
    capacity: z.number().int().min(1).max(16).nullable(),
    map: z.string().max(100).nullable(),
    source: identifier,
    schemaVersion: z.literal(2).optional(),
    edition: identifier.optional(),
    provider: identifier.optional(),
    accountNamespace: identifier.optional(),
    nativeLobbyId: identifier.optional(),
    nativeMatchId: identifier.optional(),
    platformLobbyId: identifier.optional(),
    gameBuild: identifier.optional(),
    elapsedDuration: z
      .object({ value: z.number().nonnegative().max(31536000), unit: z.literal('seconds') })
      .strict()
      .optional(),
    simulationDuration: z
      .object({ value: z.number().nonnegative().max(31536000), unit: z.literal('seconds') })
      .strict()
      .optional(),
    confidence: z.enum(['provisional', 'roster', 'confirmed']).optional(),
    bindingGeneration: identifier.optional(),
    visibility: z.enum(['public', 'private', 'unknown']).optional(),
    mode: identifier.optional(),
    replayReferences: z
      .array(
        z
          .object({
            provider: identifier,
            matchId: identifier,
            perspectiveId: identifier.optional(),
            availability: z.enum(['available', 'unavailable', 'unknown', 'expired']),
            sourceUrl: z
              .url({ protocol: /^https$/ })
              .max(4096)
              .optional(),
            expiresAt: z.number().int().nonnegative().optional(),
          })
          .strict(),
      )
      .max(32)
      .optional(),
  })
  .strict();
export const lobbyEventSchema = z
  .object({
    schemaVersion: z.union([z.literal(1), z.literal(2)]),
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
  referenceKind?: 'lobby' | 'match';
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
  confirmedBy?: string;
  confirmedAt?: number;
  availability?: string;
  failure?: string;
  suggestedResult?: 'alpha' | 'beta' | 'draw';
  identitySuggestions?: { userId: string; profileId: string; method: string; confidence: number }[];
  manualFacts?: {
    map?: string;
    elapsedSeconds?: number;
    players: { userId: string; side: number; selection: string }[];
    recordingLinks?: string[];
    declaredBy: string;
    declaredAt: number;
  };
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
    ((o.startedAt === null && o.schemaVersion !== 2) ||
      (o.startedAt !== null && o.finishedAt < o.startedAt) ||
      o.finishedAt > o.observedAt ||
      o.finishedAt > now)
  )
    return false;
  if (o.phase === 'finished')
    return o.schemaVersion === 2 || (o.startedAt !== null && o.finishedAt !== null);
  if (o.phase === 'started')
    return (o.schemaVersion === 2 || o.startedAt !== null) && o.finishedAt === null;
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
  const manual = binding.availability && binding.availability !== 'available';
  const stale = !o?.finishedAt && (!binding.lastSuccessAt || now - binding.lastSuccessAt > 180000);
  return [
    `Lobby ${binding.externalId}: ${binding.state === 'unavailable' ? 'unavailable' : (o?.phase ?? 'awaiting evidence')}${stale ? ' · status stale or not yet observed' : ''}.`,
    binding.url,
    `Binding: ${binding.id}${binding.confirmedBy ? ' · confirmed by host/captain' : ' · use /lobby confirm with this binding ID after checking the lobby'}.`,
    manual
      ? `Automatic observations: ${binding.availability}. Manual facts and captain reporting remain available.`
      : '',
    binding.lastSuccessAt
      ? `Last observed: <t:${Math.floor(binding.lastSuccessAt / 1000)}:R>.`
      : '',
    o?.elapsedDuration ? `Elapsed duration: ${o.elapsedDuration.value} seconds.` : '',
    o?.simulationDuration ? `Simulation duration: ${o.simulationDuration.value} seconds.` : '',
    binding.suggestedResult
      ? `Provider suggests ${binding.suggestedResult}; captains must confirm through the existing reporting controls.`
      : '',
    binding.identitySuggestions?.length
      ? `Suggested account links (unconfirmed): ${binding.identitySuggestions.map((p) => `<@${p.userId}> → ${p.profileId}`).join(', ')}. Use /lobby link to confirm.`
      : '',
    binding.manualFacts
      ? `Declared facts: ${binding.manualFacts.map ?? 'map unknown'}; ${binding.manualFacts.elapsedSeconds ?? '?'} seconds; ${binding.manualFacts.players.map((p) => `<@${p.userId}>: ${p.selection}`).join(', ')}.`
      : '',
    ...(o?.replayReferences ?? []).map(
      (r) =>
        `Recording ${r.perspectiveId ?? r.matchId}: ${r.expiresAt && r.expiresAt <= now ? 'expired; request /lobby replays to refresh' : r.availability}${r.sourceUrl && r.availability === 'available' && (!r.expiresAt || r.expiresAt > now) ? ` · ${r.sourceUrl}` : ''}`,
    ),
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

/** Compatibility normalizer; absent legacy facts remain unknown. */
export function normalizeObservation(o: LobbyObservation, generation?: string): LobbyObservation {
  return {
    ...o,
    schemaVersion: 2,
    referenceKind: o.referenceKind ?? 'lobby',
    ...(o.nativeLobbyId
      ? { nativeLobbyId: o.nativeLobbyId }
      : o.schemaVersion !== 2 && o.referenceKind !== 'match'
        ? { nativeLobbyId: o.externalId }
        : {}),
    confidence: 'provisional',
    ...(generation ? { bindingGeneration: generation } : {}),
  };
}
/** Supporting evidence only. This function cannot write a reported result. */
export function suggestedResult(
  binding: LobbyBinding,
  observation: LobbyObservation,
): 'alpha' | 'beta' | 'draw' | undefined {
  if (observation.phase !== 'finished' || !rosterMatches(binding, observation)) return;
  const results = [0, 1].map((side) =>
    binding.profiles
      .filter((p) => p.side === side)
      .map((p) => observation.players.find((x) => x.profileId === p.profileId)?.outcome),
  );
  if (results.some((r) => !r.length || r.some((x) => !x || x === 'unknown'))) return;
  if (results.every((r) => r.every((x) => x === 'draw'))) return 'draw';
  if (results[0].every((x) => x === 'win') && results[1].every((x) => x === 'loss')) return 'alpha';
  if (results[1].every((x) => x === 'win') && results[0].every((x) => x === 'loss')) return 'beta';
}

import { validObservation } from '../domain/lobby';
import type { GameAdapter, LobbyObservation, ProviderResult } from './types';

export interface PublicGameList {
  checkedAt: number;
  phase: 'waiting' | 'running';
  coverage: 'bounded-snapshot';
  games: Pick<
    LobbyObservation,
    | 'externalId'
    | 'referenceKind'
    | 'provider'
    | 'source'
    | 'name'
    | 'phase'
    | 'observedAt'
    | 'map'
    | 'elapsedDuration'
    | 'mode'
    | 'players'
  >[];
}

/** A bounded public view; replay URLs, account tokens and histories never enter it. */
export async function publicGameList(
  adapter: GameAdapter,
  phase: PublicGameList['phase'],
): Promise<ProviderResult<PublicGameList>> {
  const read = phase === 'running' ? adapter.discoverOngoing : adapter.discover;
  if (!read) return { status: 'unsupported' };
  const response = await read();
  if (response.status !== 'ok') return response;
  const now = Date.now();
  if (!Array.isArray(response.value) || response.value.some((o) => !validObservation(o, now)))
    return { status: 'invalid_response' };
  const games = response.value
    .filter((o) => o.visibility !== 'private')
    .filter((o) =>
      phase === 'running' ? o.phase === 'started' : ['open', 'full'].includes(o.phase),
    )
    .slice(0, 10)
    .map((o) => ({
      externalId: o.externalId,
      referenceKind: o.referenceKind,
      provider: o.provider,
      source: o.source,
      name: o.name,
      phase: o.phase,
      observedAt: o.observedAt,
      map: o.map,
      elapsedDuration: o.elapsedDuration,
      mode: o.mode,
      players: o.players.map((p) => ({
        profileId: p.profileId,
        name: p.name,
        team: p.team,
        selection: p.selection,
        selectionId: p.selectionId,
      })),
    }));
  return { status: 'ok', value: { checkedAt: now, phase, coverage: 'bounded-snapshot', games } };
}

const safe = (s: unknown) =>
  String(s ?? '')
    .replace(/[@`*_~<>\\\[\]]/g, ' ')
    .replace(/[\r\n]/g, ' ')
    .slice(0, 90);
export function publicGameListText(
  result: ProviderResult<PublicGameList>,
  maxLength = 1950,
): string {
  if (result.status !== 'ok')
    return `Public game listing: ${result.status}. Manual reporting remains available.`;
  const list = result.value;
  return [
    `${list.phase === 'running' ? 'Running games' : 'Waiting lobbies'} · checked <t:${Math.floor(list.checkedAt / 1000)}:T>. Bounded public snapshot.`,
    ...list.games.map(
      (g, i) =>
        `${i + 1}. ${safe(g.externalId)} · ${safe(g.map) || 'Map unknown'} · ${g.elapsedDuration ? `${Math.floor(g.elapsedDuration.value / 60)} min` : 'Duration unknown'}\n${g.players.map((p) => `${safe(p.name)} (${safe(p.selection) || 'faction unknown'})`).join(', ') || 'Players unknown'}`,
    ),
    ...(list.games.length ? [] : ['No matching games returned in this snapshot.']),
  ]
    .join('\n')
    .slice(0, maxLength);
}

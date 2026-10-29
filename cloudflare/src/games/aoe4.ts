import { createAgeAdapter, type AgeConfiguration } from './worlds-edge';
import type { AdapterRuntime, GameAdapter, LobbyObservation, ProviderResult } from './types';
import { providerJson } from './transport';
import {
  createAoE4WorldReader,
  decodeAoE4WorldGame,
  sameAoE4Match,
  worldReference,
  worldReferenceId,
} from './aoe4-world';
export const aoe4Configuration: AgeConfiguration = {
  id: 'aoe4',
  title: 'age4',
  host: 'https://aoe-api.worldsedgelink.com',
  edition: 'Age of Empires IV',
  selectionLabel: 'Civilization',
  simulationCounter: true,
};
export function createAoE4Adapter(runtime: AdapterRuntime = {}): GameAdapter {
  const native = createAgeAdapter(aoe4Configuration, runtime),
    world = createAoE4WorldReader(runtime);
  return {
    ...native,
    discoverOngoing: world.discoverOngoing,
    parseLobbyReference(input) {
      const value = input.trim();
      if (worldReferenceId(value)) return value;
      try {
        const url = new URL(value),
          match = /^\/players\/([1-9]\d{0,39})\/games\/([1-9]\d{0,39})\/?$/.exec(url.pathname);
        if (
          url.origin === 'https://aoe4world.com' &&
          !url.username &&
          !url.password &&
          !url.search &&
          !url.hash &&
          match
        )
          return worldReference(match[2]);
      } catch {}
      return native.parseLobbyReference(input);
    },
    observe(id, started, context) {
      return id.startsWith('aoe4world:')
        ? world.observe(id, context)
        : native.observe(id, started, context);
    },
  };
}

/** Optional display enrichment. Failure or a World 404 never replaces publisher evidence. */
export async function enrichAoE4Match(
  observation: LobbyObservation,
  profileId: string,
  runtime: AdapterRuntime = {},
): Promise<ProviderResult<LobbyObservation>> {
  if (!/^\d{1,40}$/.test(profileId) || !/^\d{1,40}$/.test(observation.nativeMatchId ?? ''))
    return { status: 'not_found' };
  const response = await (runtime.request ?? providerJson)({
    url: `https://aoe4world.com/api/v0/players/${profileId}/games/${observation.nativeMatchId}`,
    cacheTtlMs: 120000,
    maxBytes: 1000000,
  });
  if (response.status !== 'ok') return { status: 'ok', value: observation };
  const decoded = decodeAoE4WorldGame(response.value);
  // Equal numeric IDs alone do not prove cross-provider continuity.
  if (
    decoded.status !== 'ok' ||
    decoded.value.providerIds?.aoe4world !== observation.nativeMatchId ||
    !decoded.value.players.some((player) => player.profileId === profileId) ||
    !sameAoE4Match(observation, decoded.value)
  )
    return { status: 'ok', value: observation };
  const map = decoded.value.map;
  return {
    status: 'ok',
    value: {
      ...observation,
      ...(map ? { map } : {}),
      providerIds: { ...observation.providerIds, aoe4world: observation.nativeMatchId! },
    },
  };
}

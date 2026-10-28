import { createAgeAdapter, type AgeConfiguration } from './worlds-edge';
import type { AdapterRuntime, LobbyObservation, ProviderResult } from './types';
import { providerJson } from './transport';
export const aoe4Configuration: AgeConfiguration = {
  id: 'aoe4',
  title: 'age4',
  host: 'https://aoe-api.worldsedgelink.com',
  edition: 'Age of Empires IV',
  selectionLabel: 'Civilization',
  simulationCounter: true,
};
export const createAoE4Adapter = (runtime: AdapterRuntime = {}) =>
  createAgeAdapter(aoe4Configuration, runtime);

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
  const data = response.value;
  if (!data || typeof data !== 'object' || Array.isArray(data))
    return { status: 'ok', value: observation };
  const game = data as Record<string, unknown>;
  if (String(game.game_id) !== observation.nativeMatchId)
    return { status: 'ok', value: observation };
  const map =
    game.map && typeof game.map === 'object'
      ? (game.map as Record<string, unknown>).name
      : undefined;
  return {
    status: 'ok',
    value: { ...observation, ...(typeof map === 'string' && map.length <= 100 ? { map } : {}) },
  };
}

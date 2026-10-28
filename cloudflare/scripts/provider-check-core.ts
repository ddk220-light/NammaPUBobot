import {
  operations,
  type AdapterRuntime,
  type CatalogId,
  type ConnectorOperation,
  type GameAdapter,
  type JsonTransport,
  type LobbyObservation,
  type ProviderResult,
  type ReplayReference,
} from '../src/games/types';

export type ProbeStatus = 'passed' | 'failed' | 'access-blocked' | 'unsupported';
export interface ProbeRow {
  game: CatalogId;
  operation: ConnectorOperation;
  status: ProbeStatus;
  detail: string;
  checkedAt: string;
  requests: number;
}
export interface ProbeReport {
  schemaVersion: 1;
  checkedAt: string;
  scope: 'read-only provider metadata; not real-game acceptance';
  results: ProbeRow[];
  counts: Record<ProbeStatus, number>;
}
export interface ProbeOptions {
  games: readonly CatalogId[];
  createAdapter: (game: CatalogId, runtime: AdapterRuntime) => GameAdapter | undefined;
  request: JsonTransport;
  env?: Record<string, string | undefined>;
  now?: () => number;
  discoverAoE2?: () => Promise<ProviderResult<LobbyObservation[]>>;
}
const samples: Partial<Record<CatalogId, { profile?: string; match?: string }>> = {
  aoe2: { profile: '199325', match: '517004874' },
  aoe3: { profile: '10126818', match: '66376255' },
  aoe4: { profile: '4635035', match: '257612387' },
  aom: { profile: '1073774984', match: '44987874' },
  bar: { match: '4842e26a56a1bef5da914e8b4ae29c9a' },
};
const order: ConnectorOperation[] = [
  'lobbyDiscovery',
  'accountLookup',
  'matchLookup',
  'historyLookup',
  'replayReferences',
  'lobbyObservation',
  'ownershipVerification',
];
const object = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === 'object' && !Array.isArray(v);
const nonempty = (v: unknown): v is string =>
  typeof v === 'string' && v.length > 0 && v.length <= 300;
const timestamp = (v: unknown) => typeof v === 'number' && Number.isFinite(v) && v >= 946684800000;
const reference = (v: unknown, now: number, requireLink = false) => {
  if (
    !object(v) ||
    !nonempty(v.provider) ||
    !nonempty(v.matchId) ||
    !['available', 'unavailable', 'unknown', 'expired'].includes(String(v.availability))
  )
    return false;
  if (v.expiresAt !== undefined && !timestamp(v.expiresAt)) return false;
  if (v.sourceUrl !== undefined) {
    if (typeof v.sourceUrl !== 'string' || v.sourceUrl.length > 8000) return false;
    try {
      const u = new URL(v.sourceUrl);
      if (u.protocol !== 'https:' || u.username || u.password) return false;
    } catch {
      return false;
    }
  }
  return (
    v.availability !== 'available' ||
    ((!requireLink || typeof v.sourceUrl === 'string') &&
      (v.expiresAt === undefined || Number(v.expiresAt) > now))
  );
};
const observation = (v: unknown, now: number): v is LobbyObservation => {
  if (
    !object(v) ||
    !nonempty(v.externalId) ||
    !nonempty(v.source) ||
    !timestamp(v.observedAt) ||
    Number(v.observedAt) > now + 30000
  )
    return false;
  if (
    !['open', 'full', 'started', 'finished'].includes(String(v.phase)) ||
    !Array.isArray(v.players) ||
    v.players.length > 256
  )
    return false;
  if (v.startedAt !== null && !timestamp(v.startedAt)) return false;
  if (v.finishedAt !== null && !timestamp(v.finishedAt)) return false;
  if (
    typeof v.finishedAt === 'number' &&
    typeof v.startedAt === 'number' &&
    v.finishedAt < v.startedAt
  )
    return false;
  if (
    v.players.some(
      (p) =>
        !object(p) ||
        !nonempty(p.profileId) ||
        typeof p.name !== 'string' ||
        !(p.team === null || nonempty(p.team)),
    )
  )
    return false;
  if (new Set(v.players.map((p) => p.profileId)).size !== v.players.length) return false;
  for (const key of ['elapsedDuration', 'simulationDuration']) {
    const duration = v[key];
    if (
      duration !== undefined &&
      (!object(duration) ||
        duration.unit !== 'seconds' ||
        typeof duration.value !== 'number' ||
        !Number.isFinite(duration.value) ||
        duration.value < 0)
    )
      return false;
  }
  return (
    v.replayReferences === undefined ||
    (Array.isArray(v.replayReferences) && v.replayReferences.every((r) => reference(r, now)))
  );
};
const semanticResult = (operation: ConnectorOperation, value: unknown, now: number): boolean => {
  switch (operation) {
    case 'accountLookup':
      return (
        object(value) &&
        nonempty(value.id) &&
        typeof value.name === 'string' &&
        typeof value.url === 'string'
      );
    case 'lobbyObservation':
    case 'matchLookup':
      return observation(value, now);
    case 'lobbyDiscovery':
      return Array.isArray(value) && value.every((v) => observation(v, now));
    case 'historyLookup':
      return (
        Array.isArray(value) &&
        value.every(
          (v) =>
            observation(v, now) ||
            (object(v) &&
              nonempty(v.source) &&
              timestamp(v.observedAt) &&
              nonempty(v.accountId) &&
              (v.occurredAt === null || timestamp(v.occurredAt))),
        )
      );
    case 'replayReferences':
      return (
        Array.isArray(value) && value.length <= 256 && value.every((r) => reference(r, now, true))
      );
    default:
      return false;
  }
};
function resultStatus(
  result: ProviderResult<unknown>,
  operation: ConnectorOperation,
  now: number,
): Pick<ProbeRow, 'status' | 'detail'> {
  if (result.status === 'unsupported')
    return { status: 'failed', detail: 'declared_operation_unsupported' };
  if (result.status === 'authentication_required')
    return { status: 'access-blocked', detail: 'credentials_rejected_or_required' };
  if (result.status !== 'ok') return { status: 'failed', detail: result.status };
  if (!semanticResult(operation, result.value, now))
    return { status: 'failed', detail: 'invalid_normalized_response' };
  return {
    status: 'passed',
    detail:
      Array.isArray(result.value) && result.value.length === 0
        ? 'valid_empty_result'
        : 'valid_metadata_response',
  };
}

/** No raw response, provider URL, profile history, exception message or credential enters a report. */
export async function runProviderChecks(options: ProbeOptions): Promise<ProbeReport> {
  const now = options.now ?? Date.now;
  const env = options.env ?? {};
  const report: ProbeReport = {
    schemaVersion: 1,
    checkedAt: new Date(now()).toISOString(),
    scope: 'read-only provider metadata; not real-game acceptance',
    results: [],
    counts: { passed: 0, failed: 0, 'access-blocked': 0, unsupported: 0 },
  };
  for (const game of options.games) {
    let requests = 0;
    const request: JsonTransport = async (input) => {
      if (++requests > 12) return { status: 'unavailable' };
      return options.request({
        ...input,
        maxBytes: Math.min(input.maxBytes ?? 2000000, 2000000),
        cacheTtlMs: 0,
      });
    };
    const runtime: AdapterRuntime = {
      request,
      generalsOnlineApiKey: env.GENERALS_ONLINE_API_KEY,
      battleNetAccessToken: env.BATTLE_NET_ACCESS_TOKEN,
    };
    const adapter = options.createAdapter(game, runtime);
    const prefix = `CONNECTOR_PROBE_${game.toUpperCase()}`;
    let profile = env[`${prefix}_PROFILE_ID`]?.trim() || samples[game]?.profile;
    const match = env[`${prefix}_MATCH_ID`]?.trim() || samples[game]?.match;
    let publicLobby: LobbyObservation | undefined;
    for (const operation of order) {
      const before = requests;
      let outcome: Pick<ProbeRow, 'status' | 'detail'>;
      const capability = adapter?.manifest.capabilities[operation] ?? 'unsupported';
      if (capability === 'unsupported')
        outcome = { status: 'unsupported', detail: 'provider_has_no_supported_operation' };
      else if (operation === 'ownershipVerification')
        outcome = { status: 'access-blocked', detail: 'interactive_sign_in_required' };
      else if (
        (game === 'zero_hour' && !runtime.generalsOnlineApiKey) ||
        (game === 'sc2' && !runtime.battleNetAccessToken)
      )
        outcome =
          game === 'sc2' && env.CONNECTOR_PROBE_SC2_AUTH_ERROR
            ? {
                status: ['throttled', 'provider_unavailable', 'invalid_response'].includes(
                  env.CONNECTOR_PROBE_SC2_AUTH_ERROR,
                )
                  ? 'failed'
                  : 'access-blocked',
                detail: `credential_setup_${['authorization_revoked', 'credentials_required', 'throttled', 'provider_unavailable', 'invalid_response'].includes(env.CONNECTOR_PROBE_SC2_AUTH_ERROR) ? env.CONNECTOR_PROBE_SC2_AUTH_ERROR : 'failed'}`,
              }
            : { status: 'access-blocked', detail: 'integration_credentials_missing' };
      else {
        let result: ProviderResult<unknown> | undefined;
        try {
          switch (operation) {
            case 'lobbyDiscovery':
              if (game === 'aoe2' && options.discoverAoE2) {
                requests++;
                result = await options.discoverAoE2();
              } else
                result = adapter?.discover ? await adapter.discover() : { status: 'unsupported' };
              if (result.status === 'ok' && Array.isArray(result.value)) {
                publicLobby = result.value.find((v) => observation(v, now()));
                profile ??= publicLobby?.players[0]?.profileId;
              }
              break;
            case 'accountLookup':
              if (profile) result = await adapter!.profile(profile);
              break;
            case 'matchLookup':
              if (match)
                result = adapter?.match ? await adapter.match(match) : { status: 'unsupported' };
              break;
            case 'historyLookup':
              if (profile) {
                const supporting = adapter as GameAdapter & {
                  supportingHistory?: (id: string) => Promise<ProviderResult<unknown>>;
                };
                result = adapter?.history
                  ? await adapter.history(profile)
                  : supporting?.supportingHistory
                    ? await supporting.supportingHistory(profile)
                    : { status: 'unsupported' };
              }
              break;
            case 'replayReferences':
              if (match)
                result = adapter?.replays
                  ? await adapter.replays(match)
                  : { status: 'unsupported' };
              break;
            case 'lobbyObservation':
              if (game === 'aoe2' && match)
                result = await adapter!.observe(match, true, { nativeMatchId: match });
              else if (publicLobby)
                result = await adapter!.observe(publicLobby.externalId, !!publicLobby.startedAt, {
                  previous: publicLobby,
                  attachedAt: now(),
                  profileIds: publicLobby.players.map((p) => p.profileId),
                });
              break;
          }
          outcome = result
            ? resultStatus(result, operation, now())
            : {
                status: 'access-blocked',
                detail:
                  operation === 'lobbyObservation'
                    ? 'no_public_lobby_sample'
                    : 'sample_identifier_required',
              };
        } catch {
          outcome = { status: 'failed', detail: 'operation_exception' };
        }
      }
      if (requests > 12 && requests > before)
        outcome = { status: 'failed', detail: 'per_game_request_budget_exceeded' };
      const row = {
        game,
        operation,
        ...outcome,
        checkedAt: new Date(now()).toISOString(),
        requests: Math.max(0, Math.min(requests, 12) - before),
      };
      report.results.push(row);
      report.counts[row.status]++;
    }
  }
  return report;
}
// Exported for compact operator summaries, not provider payload inspection.
export const checkedOperations = operations;

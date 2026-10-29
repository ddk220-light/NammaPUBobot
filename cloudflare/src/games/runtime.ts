import { battleNetClientToken, gameAccountFailure } from '../auth/game-accounts';
import type { Env } from '../env';
import { gameCapabilities } from './catalog';
import { gameAdapter } from './registry';
import type { AdapterRuntime, ConnectorOperation, GameAdapter, ProviderResult } from './types';
import { providerJson } from './transport';
/** Provider routes are the final boundary: adapter-internal calls must respect switches too. */
export function requestOperation(url: string): ConnectorOperation | undefined {
  let u: URL;
  try {
    u = new URL(url);
  } catch {
    return;
  }
  const path = u.pathname;
  if (u.hostname === 'website-backend.w3champions.com') {
    if (path.startsWith('/api/players/')) return 'accountLookup';
    if (path.startsWith('/api/matches/ongoing')) return 'ongoingDiscovery';
    if (path === '/api/matches/search') return 'historyLookup';
    if (path.startsWith('/api/matches/')) return 'matchLookup';
  }
  if (['aoe-api.worldsedgelink.com', 'athens-live-api.worldsedgelink.com'].includes(u.hostname)) {
    const name = path.split('/').pop();
    return (
      {
        findAdvertisements: 'lobbyDiscovery',
        getPersonalStat: 'accountLookup',
        getMatchHistory: 'matchLookup',
        getRecentMatchHistory: 'historyLookup',
        getReplayFiles: 'replayReferences',
      } as Record<string, ConnectorOperation>
    )[name ?? ''];
  }
  if (u.hostname === 'data.aoe2companion.com') {
    if (/^\/api\/profiles(?:\/|$)/.test(path)) return 'accountLookup';
    if (/^\/api\/matches\/\d+\/?$/.test(path)) return 'matchLookup';
    if (/^\/api\/matches\/?$/.test(path)) return 'historyLookup';
  }
  if (u.hostname === 'api.bar-rts.com') {
    if (path === '/battles') return 'lobbyDiscovery';
    if (/^\/replays\/[a-f0-9]{32}$/.test(path)) return 'matchLookup';
    if (path === '/replays') return 'historyLookup';
    if (path === '/cached-users') return 'accountLookup';
  }
  if (u.hostname === 'api.playgenerals.online') {
    if (/\/Lobbies(?:\/|$)/i.test(path)) return 'lobbyDiscovery';
    if (/\/MatchHistory(?:\/|$)/i.test(path)) return 'matchLookup';
  }
  if (['us.api.blizzard.com', 'eu.api.blizzard.com', 'kr.api.blizzard.com'].includes(u.hostname)) {
    if (/^\/sc2\/legacy\/profile\/\d+\/\d+\/\d+\/matches$/.test(path)) return 'historyLookup';
    if (/^\/sc2\/(?:profile|player)\//.test(path)) return 'accountLookup';
  }
  if (u.hostname === 'aoe4world.com') {
    if (path === '/api/v0/games') return 'ongoingDiscovery';
    if (/\/games\/(?:last)?$/.test(path) || path.endsWith('/games')) return 'historyLookup';
    if (/\/games\/\d+$/.test(path)) return 'matchLookup';
    if (/^\/api\/v0\/players\/\d+$/.test(path)) return 'accountLookup';
  }
}
const providerHost = (id: string, accountId?: string) =>
  id === 'wc3'
    ? 'website-backend.w3champions.com'
    : id === 'aoe2'
      ? 'data.aoe2companion.com'
      : id === 'aom'
        ? 'athens-live-api.worldsedgelink.com'
        : ['aoe3', 'aoe4'].includes(id)
          ? 'aoe-api.worldsedgelink.com'
          : id === 'bar'
            ? 'api.bar-rts.com'
            : id === 'zero_hour'
              ? 'api.playgenerals.online'
              : accountId?.startsWith('2:')
                ? 'eu.api.blizzard.com'
                : accountId?.startsWith('3:')
                  ? 'kr.api.blizzard.com'
                  : 'us.api.blizzard.com';
export function runtimeAdapter(
  env: Env,
  catalogId: string | null | undefined,
  capabilities: Partial<Record<ConnectorOperation, boolean>> = {},
  options: { publicListing?: boolean } = {},
) {
  let adapter: GameAdapter | undefined;
  const availability = (
    operation: ConnectorOperation,
    acceptanceGate = true,
    provider?: string,
  ) => {
    if (capabilities[operation] === false) return 'disabled';
    const status = connectorAvailability(env, adapter!, operation, undefined, provider);
    // Acceptance gates automatic entry points, not supporting reads for permitted profile/match operations.
    return status === 'acceptance_required' &&
      (!acceptanceGate ||
        (options.publicListing && ['lobbyDiscovery', 'ongoingDiscovery'].includes(operation)))
      ? 'available'
      : status;
  };
  const blocked = (status: string): ProviderResult<never> | undefined =>
    status === 'available'
      ? undefined
      : { status: status === 'credentials_required' ? 'authentication_required' : 'unsupported' };
  const runtime: AdapterRuntime = {
    generalsOnlineApiKey: env.GENERALS_ONLINE_API_KEY,
    aoe2Discovery: () => env.AOE2_LOBBIES.getByName(env.DISCORD_APPLICATION_ID ?? 'local').browse(),
    aoe2OngoingDiscovery: () =>
      env.AOE2_LOBBIES.getByName(env.DISCORD_APPLICATION_ID ?? 'local').browseOngoing(),
    request: async (request) => {
      const inferred = requestOperation(request.url);
      const u = new URL(request.url);
      const operation =
        request.operation &&
        u.hostname === 'api.bar-rts.com' &&
        u.pathname === '/battles' &&
        ['lobbyDiscovery', 'ongoingDiscovery'].includes(request.operation)
          ? request.operation
          : inferred;
      if (!adapter || !operation) return { status: 'unsupported' };
      const disabled = (env.CONNECTOR_DISABLED ?? '').split(',').map((s) => s.trim());
      if (disabled.includes(u.hostname)) return { status: 'unsupported' };
      const provider = u.hostname === 'aoe4world.com' ? 'aoe4world' : undefined;
      const failure = blocked(availability(operation, false, provider));
      if (failure) return failure;
      return env.PROVIDERS
        ? env.PROVIDERS.getByName(new URL(request.url).hostname).request(request)
        : providerJson(request);
    },
  };
  adapter = gameAdapter(catalogId, runtime);
  if (!adapter) return;
  if (adapter.id === 'sc2' && env.BATTLENET_CLIENT_ID && env.BATTLENET_CLIENT_SECRET) {
    const withToken = async <T>(
      call: (a: GameAdapter) => Promise<ProviderResult<T>>,
    ): Promise<ProviderResult<T>> => {
      try {
        const token = env.PROVIDERS
          ? await env.PROVIDERS.getByName('blizzard-auth').battleNetToken()
          : { status: 'ok' as const, value: await battleNetClientToken(env) };
        if (token.status !== 'ok') return token;
        const result = await call(
          gameAdapter('sc2', { ...runtime, battleNetAccessToken: token.value.accessToken })!,
        );
        if (result.status === 'authentication_required')
          await env.PROVIDERS?.getByName('blizzard-auth').invalidateBattleNetToken(
            token.value.accessToken,
          );
        return result;
      } catch (error) {
        return gameAccountFailure(error);
      }
    };
    adapter.profile = (id) => withToken((a) => a.profile(id));
    adapter.supportingHistory = (id) => withToken((a) => a.supportingHistory!(id));
  }
  // Every application call checks its capability before obtaining tokens or making reads.
  // Integration probes use registry adapters directly, separately from release gates.
  const run = async <T>(
    operation: ConnectorOperation,
    call: () => Promise<ProviderResult<T>>,
    accountId?: string,
  ): Promise<ProviderResult<T>> => {
    const failure = blocked(
      availability(operation, true, accountId?.startsWith('aoe4world:') ? 'aoe4world' : undefined),
    );
    if (failure) return failure;
    let result: ProviderResult<T>;
    try {
      result = await call();
    } catch {
      result = { status: 'unavailable' };
    }
    const outcomeHost =
      adapter!.id === 'aoe4' &&
      (operation === 'ongoingDiscovery' || accountId?.startsWith('aoe4world:'))
        ? 'aoe4world.com'
        : providerHost(adapter!.id, accountId);
    await env.PROVIDERS?.getByName(outcomeHost)
      .recordOutcome(result.status, result.status === 'ok' ? undefined : result.retryAfterMs)
      .catch(() => {});
    if (
      result.status === 'ok' &&
      ((operation === 'ongoingDiscovery' && !options.publicListing) ||
        (adapter!.id === 'wc3' && operation === 'lobbyObservation'))
    ) {
      const accepted = (value: unknown) => {
        const mode = (value as { mode?: string })?.mode;
        const scopes = (env.CONNECTOR_ACCEPTED_CAPABILITIES ?? '').split(',').map((s) => s.trim());
        return (
          scopes.includes(`${adapter!.id}.${operation}`) ||
          (!!mode && scopes.includes(`${adapter!.id}.${operation}:${mode}`))
        );
      };
      if (Array.isArray(result.value))
        result = { status: 'ok', value: result.value.filter(accepted) as T };
      else if (!accepted(result.value)) return { status: 'unsupported' };
    }
    if (result.status === 'ok' && availability('replayReferences', false) !== 'available') {
      const removeReferences = (value: unknown): unknown => {
        if (Array.isArray(value)) return value.map(removeReferences);
        if (value && typeof value === 'object' && Object.hasOwn(value, 'replayReferences')) {
          const copy = { ...value } as Record<string, unknown>;
          delete copy.replayReferences;
          return copy;
        }
        return value;
      };
      return { status: 'ok', value: removeReferences(result.value) as T };
    }
    return result;
  };
  // Do not replace methods on the provider object itself: its internal calls are dependencies,
  // not fresh top-level operations (for example AoE2 match() delegates to its own observe()).
  const wrapped = { ...adapter };
  const profile = adapter.profile,
    observe = adapter.observe;
  wrapped.profile = (id) => run('accountLookup', () => profile(id), id);
  wrapped.observe = (id, started, context) =>
    run('lobbyObservation', () => observe(id, started, context), id);
  const methods = {
    discover: 'lobbyDiscovery',
    discoverOngoing: 'ongoingDiscovery',
    match: 'matchLookup',
    history: 'historyLookup',
    replays: 'replayReferences',
    supportingHistory: 'historyLookup',
    resolveSteam: 'accountLookup',
  } as const;
  for (const name of Object.keys(methods) as (keyof typeof methods)[]) {
    const original = adapter[name];
    if (original)
      (wrapped as unknown as Record<string, unknown>)[name] = (...args: [string]) =>
        run(
          methods[name],
          () => (original as (id: string) => Promise<ProviderResult<unknown>>)(...args),
          args[0],
        );
  }
  return wrapped;
}
export function connectorAvailability(
  env: Env,
  adapter: GameAdapter,
  operation: ConnectorOperation,
  mode?: string,
  provider?: string,
): 'available' | 'unsupported' | 'credentials_required' | 'acceptance_required' | 'disabled' {
  const disabled = (env.CONNECTOR_DISABLED ?? '').split(',').map((s) => s.trim());
  const ownershipProvider =
    operation !== 'ownershipVerification'
      ? null
      : ['aoe2', 'aoe3', 'aoe4', 'aom', 'northgard', 'tempest_rising'].includes(adapter.id)
        ? 'steam'
        : ['sc2', 'wc3'].includes(adapter.id)
          ? 'battlenet'
          : adapter.id === 'bar'
            ? 'bar-oauth'
            : null;
  if (
    disabled.includes(adapter.id) ||
    disabled.includes(
      provider ??
        (adapter.id === 'aoe4' && operation === 'ongoingDiscovery'
          ? 'aoe4world'
          : adapter.manifest.provider),
    ) ||
    disabled.includes(`${adapter.id}.${operation}`) ||
    (ownershipProvider && disabled.includes(ownershipProvider))
  )
    return 'disabled';
  const capability = adapter.manifest.capabilities[operation];
  if (capability === 'unsupported') return capability;
  if (
    gameCapabilities(adapter.id)?.lifecycle === 'legacy' &&
    ['lobbyObservation', 'lobbyDiscovery', 'ongoingDiscovery'].includes(operation)
  )
    return 'disabled';
  if (capability === 'credentials_required') {
    if (adapter.id === 'zero_hour' && !env.GENERALS_ONLINE_API_KEY) return capability;
    if (
      ['sc2', 'wc3'].includes(adapter.id) &&
      (!env.BATTLENET_CLIENT_ID || !env.BATTLENET_CLIENT_SECRET)
    )
      return capability;
    if (adapter.id === 'bar' && operation === 'ownershipVerification' && !env.BAR_CLIENT_ID)
      return capability;
  }
  if (
    ['lobbyObservation', 'lobbyDiscovery', 'ongoingDiscovery'].includes(operation) &&
    adapter.manifest.acceptance !== 'legacy' &&
    !(env.CONNECTOR_ACCEPTED_GAMES ?? '')
      .split(',')
      .map((s) => s.trim())
      .includes(adapter.id)
  )
    return 'acceptance_required';
  if (
    (operation === 'ongoingDiscovery' ||
      (adapter.id === 'wc3' && operation === 'lobbyObservation')) &&
    !(env.CONNECTOR_ACCEPTED_CAPABILITIES ?? '')
      .split(',')
      .some(
        (s) =>
          s.trim() === `${adapter.id}.${operation}` ||
          (mode
            ? s.trim() === `${adapter.id}.${operation}:${mode}`
            : s.trim().startsWith(`${adapter.id}.${operation}:`)),
      )
  )
    return 'acceptance_required';
  return 'available';
}

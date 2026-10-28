import type { CatalogId, ProviderProfile, ProviderResult } from '../games/types';
import { parseProviderJson, retryDelay } from '../games/transport';

export type OwnershipProvider = 'steam' | 'battlenet' | 'bar';
export interface GameAccountCredentials {
  BATTLENET_CLIENT_ID?: string;
  BATTLENET_CLIENT_SECRET?: string;
  BAR_CLIENT_ID?: string;
  BAR_CLIENT_SECRET?: string;
}
export interface GameVerificationState {
  provider: OwnershipProvider;
  communityId: string;
  gameId: string;
  catalogId: CatalogId;
  discordUserId: string;
  sessionId: string;
  expiresAt: number;
  redirectUri: string;
  codeVerifier?: string;
}
export interface GameAccountProof {
  communityId: string;
  gameId: string;
  catalogId: CatalogId;
  discordUserId: string;
  provider: 'steam' | 'battle-net' | 'bar';
  externalId: string;
  url: string;
  namespace: string;
  edition: string;
  platformId?: string;
  verifiedBy: 'steam-openid' | 'battlenet-oauth' | 'bar-oauth';
  verifiedAt: number;
}
export interface GameAccountChoiceState extends GameVerificationState {
  accountId: string;
  profiles: ProviderProfile[];
  /** Failed regional requests are displayed rather than implying complete coverage. */
  unavailableRegions: string[];
}
export class GameAccountError extends Error {
  constructor(
    public code: string,
    message: string,
    public status = 400,
    public retryAfterMs?: number,
  ) {
    super(message);
  }
}
/** Keep authentication outages distinct from revoked credentials in provider diagnostics. */
export function gameAccountFailure(
  error: unknown,
): Exclude<ProviderResult<never>, { status: 'ok' }> {
  const status =
    error instanceof GameAccountError
      ? ['credentials_required', 'authorization_revoked', 'invalid_code'].includes(error.code)
        ? 'authentication_required'
        : error.code === 'throttled'
          ? 'throttled'
          : error.code === 'invalid_response'
            ? 'invalid_response'
            : 'unavailable'
      : 'unavailable';
  return {
    status,
    ...(error instanceof GameAccountError && error.retryAfterMs
      ? { retryAfterMs: error.retryAfterMs }
      : {}),
  };
}
export const steamEndpoint = 'https://steamcommunity.com/openid/login';
const openidNamespace = 'http://specs.openid.net/auth/2.0';
const identifierSelect = 'http://specs.openid.net/auth/2.0/identifier_select';
const oauthEndpoints = {
  battlenet: {
    authorization: 'https://oauth.battle.net/authorize',
    token: 'https://oauth.battle.net/token',
    userinfo: 'https://oauth.battle.net/userinfo',
    scope: 'openid sc2.profile',
  },
  bar: {
    authorization: 'https://server4.beyondallreason.info/oauth/authorize',
    token: 'https://server4.beyondallreason.info/oauth/token',
    userinfo: 'https://server4.beyondallreason.info/oauth/userinfo',
    scope: 'profile',
  },
} as const;
export const randomState = () =>
  Array.from(crypto.getRandomValues(new Uint8Array(32)), (b) =>
    b.toString(16).padStart(2, '0'),
  ).join('');
export const validState = (value?: string | null): value is string =>
  !!value && /^[a-f0-9]{64}$/.test(value);
export function gameAccountAvailability(
  provider: OwnershipProvider,
  env: GameAccountCredentials,
): boolean {
  return (
    provider === 'steam' ||
    (provider === 'battlenet'
      ? !!env.BATTLENET_CLIENT_ID && !!env.BATTLENET_CLIENT_SECRET
      : !!env.BAR_CLIENT_ID)
  );
}
export function supportsOwnership(catalog: CatalogId, provider: OwnershipProvider): boolean {
  return provider === 'steam'
    ? ['aoe2', 'aoe3', 'aoe4', 'aom', 'northgard', 'tempest_rising'].includes(catalog)
    : provider === 'battlenet'
      ? catalog === 'sc2'
      : catalog === 'bar';
}
export async function pkceChallenge(verifier: string): Promise<string> {
  const hash = new Uint8Array(
    await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier)),
  );
  return btoa(String.fromCharCode(...hash))
    .replaceAll('+', '-')
    .replaceAll('/', '_')
    .replaceAll('=', '');
}
export async function authorizationUrl(
  state: GameVerificationState,
  nonce: string,
  env: GameAccountCredentials,
): Promise<string> {
  if (state.provider === 'steam') {
    const url = new URL(steamEndpoint);
    url.search = new URLSearchParams({
      'openid.ns': openidNamespace,
      'openid.mode': 'checkid_setup',
      'openid.return_to': state.redirectUri,
      'openid.realm': new URL(state.redirectUri).origin,
      'openid.identity': identifierSelect,
      'openid.claimed_id': identifierSelect,
    }).toString();
    return url.href;
  }
  if (!gameAccountAvailability(state.provider, env))
    throw new GameAccountError(
      'credentials_required',
      'This identity provider needs operator credentials. Declared linking remains available.',
      503,
    );
  const config = oauthEndpoints[state.provider];
  const url = new URL(config.authorization);
  const query = new URLSearchParams({
    client_id: (state.provider === 'battlenet' ? env.BATTLENET_CLIENT_ID : env.BAR_CLIENT_ID)!,
    response_type: 'code',
    redirect_uri: state.redirectUri,
    scope: config.scope,
    state: nonce,
  });
  // BAR advertises S256 support. Battle.net's published authorization-code contract
  // does not advertise PKCE; it uses the confidential client secret and one-time state.
  if (state.provider === 'bar') {
    if (!state.codeVerifier)
      throw new GameAccountError('invalid_state', 'Start account verification again.');
    query.set('code_challenge', await pkceChallenge(state.codeVerifier));
    query.set('code_challenge_method', 'S256');
  }
  url.search = query.toString();
  return url.href;
}
async function boundedBody(response: Response, limit = 131072): Promise<string> {
  const reader = response.body?.getReader();
  if (!reader)
    throw new GameAccountError(
      'invalid_response',
      'The identity provider returned an empty response.',
      502,
    );
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.length;
      if (size > limit)
        throw new GameAccountError(
          'invalid_response',
          'The identity provider response was too large.',
          502,
        );
      chunks.push(value);
    }
  } finally {
    await reader.cancel().catch(() => {});
  }
  const result = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    result.set(chunk, offset);
    offset += chunk.length;
  }
  return new TextDecoder().decode(result);
}
async function fetchProvider(url: string, init: RequestInit, request = fetch): Promise<Response> {
  let response: Response;
  try {
    response = await request(url, {
      ...init,
      // Workerd supports manual/follow; manual ensures authorization headers never cross hosts.
      redirect: 'manual',
      signal: AbortSignal.timeout(8000),
    });
  } catch {
    throw new GameAccountError(
      'provider_unavailable',
      'The identity provider is temporarily unavailable. Start verification again.',
      503,
    );
  }
  if (!response.ok) await response.body?.cancel().catch(() => {});
  if (response.status === 401 || response.status === 403)
    throw new GameAccountError(
      'authorization_revoked',
      'The provider rejected authorization. Start verification again.',
      401,
    );
  if (response.status === 429)
    throw new GameAccountError(
      'throttled',
      'The identity provider is limiting requests. Retry later.',
      503,
      retryDelay(response.headers.get('Retry-After')) ?? 120000,
    );
  if (!response.ok)
    throw new GameAccountError(
      'provider_unavailable',
      'The identity provider could not complete verification. Start again.',
      503,
      retryDelay(response.headers.get('Retry-After')),
    );
  return response;
}
/** A successful provider assertion proves Steam control, not ownership of an arbitrary game profile. */
export async function verifySteamAssertion(
  query: URLSearchParams,
  state: GameVerificationState,
  now = Date.now(),
  request = fetch,
): Promise<string> {
  for (const key of new Set(query.keys()))
    if (query.getAll(key).length !== 1)
      throw new GameAccountError('invalid_assertion', 'Duplicate OpenID parameters.');
  const identity = query.get('openid.claimed_id');
  const id = /^https:\/\/steamcommunity\.com\/openid\/id\/([0-9]{17})$/.exec(identity ?? '')?.[1];
  const signed = new Set((query.get('openid.signed') ?? '').split(','));
  const required = [
    'op_endpoint',
    'claimed_id',
    'identity',
    'return_to',
    'response_nonce',
    'assoc_handle',
  ];
  const nonce = query.get('openid.response_nonce') ?? '';
  const stamp = /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ/.exec(nonce)?.[0];
  const time = stamp ? Date.parse(stamp) : NaN;
  if (
    state.provider !== 'steam' ||
    query.get('openid.ns') !== openidNamespace ||
    query.get('openid.mode') !== 'id_res' ||
    query.get('openid.op_endpoint') !== steamEndpoint ||
    !id ||
    query.get('openid.identity') !== identity ||
    query.get('openid.return_to') !== state.redirectUri ||
    required.some((key) => !signed.has(key) || !query.get(`openid.${key}`)) ||
    !Number.isFinite(time) ||
    time > now + 60000 ||
    now - time > 600000 ||
    !query.get('openid.sig') ||
    (query.get('openid.sig')?.length ?? 0) > 2048
  )
    throw new GameAccountError(
      'invalid_assertion',
      'Steam did not provide a valid account assertion. Start verification again.',
    );
  const body = new URLSearchParams();
  for (const [key, value] of query) if (key.startsWith('openid.')) body.set(key, value);
  body.set('openid.mode', 'check_authentication');
  const response = await fetchProvider(
    steamEndpoint,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body,
    },
    request,
  );
  const text = await boundedBody(response, 4096);
  const fields = new Map(
    text
      .trim()
      .split(/\r?\n/)
      .map((line) => {
        const index = line.indexOf(':');
        return [line.slice(0, index), line.slice(index + 1)] as const;
      }),
  );
  if (fields.get('is_valid') !== 'true' || fields.get('ns') !== openidNamespace)
    throw new GameAccountError(
      'invalid_assertion',
      'Steam rejected the account assertion. Start verification again.',
    );
  return id;
}
async function providerJson(
  url: string,
  init: RequestInit,
  request = fetch,
): Promise<Record<string, unknown>> {
  const response = await fetchProvider(url, init, request);
  let value: unknown;
  try {
    value = parseProviderJson(await boundedBody(response));
  } catch (error) {
    if (error instanceof GameAccountError) throw error;
    throw new GameAccountError(
      'invalid_response',
      'The identity provider returned an invalid response.',
      502,
    );
  }
  if (!value || typeof value !== 'object' || Array.isArray(value))
    throw new GameAccountError(
      'invalid_response',
      'The identity provider returned an invalid response.',
      502,
    );
  const result = value as Record<string, unknown>;
  if (result.error) {
    const code = String(result.error);
    if (['invalid_client', 'unauthorized_client', 'invalid_token', 'access_denied'].includes(code))
      throw new GameAccountError(
        'authorization_revoked',
        'The provider rejected authorization. Start verification again.',
        401,
      );
    if (code === 'invalid_grant')
      throw new GameAccountError(
        'invalid_code',
        'The authorization expired or was already used. Start verification again.',
      );
    if (['temporarily_unavailable', 'server_error'].includes(code))
      throw new GameAccountError(
        'provider_unavailable',
        'The identity provider is temporarily unavailable. Retry later.',
        503,
      );
    if (['slow_down', 'rate_limit_exceeded'].includes(code))
      throw new GameAccountError(
        'throttled',
        'The identity provider is limiting requests. Retry later.',
        503,
        120000,
      );
    throw new GameAccountError(
      'invalid_response',
      'The identity provider returned an unsupported error response.',
      502,
    );
  }
  return result;
}
const stableId = (value: unknown) =>
  typeof value === 'string' && /^[1-9]\d{0,19}$/.test(value)
    ? value
    : typeof value === 'number' && Number.isSafeInteger(value) && value > 0
      ? String(value)
      : null;
export interface OAuthIdentity {
  subject: string;
  name: string;
  accessToken: string;
}
/** Public SC2 API access only; this grant cannot prove a Discord user's ownership. */
export async function battleNetClientToken(
  env: GameAccountCredentials,
  request = fetch,
): Promise<{ accessToken: string; expiresAt: number }> {
  if (!gameAccountAvailability('battlenet', env))
    throw new GameAccountError(
      'credentials_required',
      'Battle.net API credentials are not configured.',
      503,
    );
  const token = await providerJson(
    oauthEndpoints.battlenet.token,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        Authorization: `Basic ${btoa(`${env.BATTLENET_CLIENT_ID}:${env.BATTLENET_CLIENT_SECRET}`)}`,
      },
      body: new URLSearchParams({ grant_type: 'client_credentials' }),
    },
    request,
  );
  if (
    typeof token.access_token !== 'string' ||
    !token.access_token ||
    token.access_token.length > 8192 ||
    typeof token.expires_in !== 'number' ||
    !Number.isFinite(token.expires_in) ||
    token.expires_in <= 0 ||
    (token.token_type !== undefined && String(token.token_type).toLowerCase() !== 'bearer') ||
    token.error
  )
    throw new GameAccountError(
      'invalid_response',
      'Battle.net did not issue usable API authorization.',
      502,
    );
  return {
    accessToken: token.access_token,
    expiresAt: Date.now() + Math.min(token.expires_in, 86400) * 1000,
  };
}
/** Tokens are returned only to the callback's stack; callers must not persist or log them. */
export async function completeGameOAuth(
  state: GameVerificationState,
  code: string,
  env: GameAccountCredentials,
  request = fetch,
): Promise<OAuthIdentity> {
  if (
    state.provider === 'steam' ||
    !gameAccountAvailability(state.provider, env) ||
    !code ||
    code.length > 2048
  )
    throw new GameAccountError('invalid_code', 'Start account verification again.');
  const config = oauthEndpoints[state.provider];
  const clientId = (state.provider === 'battlenet' ? env.BATTLENET_CLIENT_ID : env.BAR_CLIENT_ID)!;
  const clientSecret =
    state.provider === 'battlenet' ? env.BATTLENET_CLIENT_SECRET : env.BAR_CLIENT_SECRET;
  const body = new URLSearchParams({
    grant_type: 'authorization_code',
    code,
    redirect_uri: state.redirectUri,
    client_id: clientId,
  });
  const headers: Record<string, string> = { 'Content-Type': 'application/x-www-form-urlencoded' };
  if (clientSecret) headers.Authorization = `Basic ${btoa(`${clientId}:${clientSecret}`)}`;
  if (state.provider === 'bar') {
    if (!state.codeVerifier)
      throw new GameAccountError('invalid_state', 'Start account verification again.');
    body.set('code_verifier', state.codeVerifier);
  }
  const token = await providerJson(config.token, { method: 'POST', headers, body }, request);
  if (
    typeof token.access_token !== 'string' ||
    token.access_token.length < 1 ||
    token.access_token.length > 8192 ||
    (token.token_type !== undefined && String(token.token_type).toLowerCase() !== 'bearer') ||
    token.error
  )
    throw new GameAccountError(
      'invalid_response',
      'The identity provider did not issue a usable authorization.',
      502,
    );
  const user = await providerJson(
    config.userinfo,
    { headers: { Authorization: `Bearer ${token.access_token}` } },
    request,
  );
  const subject = stableId(user.sub);
  if (!subject || user.error)
    throw new GameAccountError(
      'invalid_response',
      'The identity provider did not supply a stable account ID.',
      502,
    );
  return {
    subject,
    name: String(user.preferred_username ?? user.battletag ?? subject).slice(0, 100),
    accessToken: token.access_token,
  };
}

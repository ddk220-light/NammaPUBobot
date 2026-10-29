import type { JsonTransport, ProviderResult } from './types';

/** Preserve integer identifiers before JSON.parse can round them. Never repair malformed JSON. */
export function parseProviderJson(source: string): unknown {
  const safe = source.replace(
    /"(?:\\.|[^"\\])*"|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/g,
    (token) => {
      if (token.startsWith('"')) return token;
      const n = Number(token);
      if (!Number.isFinite(n)) throw Error('Invalid numeric value');
      if (Number.isInteger(n) && !Number.isSafeInteger(n)) {
        if (!/^-?\d+$/.test(token)) throw Error('Unsafe numeric notation');
        return JSON.stringify(token);
      }
      return token;
    },
  );
  return JSON.parse(safe);
}
export const providerHosts = new Set([
  'website-backend.w3champions.com',
  'data.aoe2companion.com',
  'aoe-api.worldsedgelink.com',
  'athens-live-api.worldsedgelink.com',
  'aoe4world.com',
  'api.bar-rts.com',
  'api.playgenerals.online',
  'us.api.blizzard.com',
  'eu.api.blizzard.com',
  'kr.api.blizzard.com',
]);
export function allowedProviderUrl(input: string): boolean {
  try {
    const u = new URL(input);
    return (
      u.protocol === 'https:' &&
      !u.username &&
      !u.password &&
      !u.port &&
      !u.hash &&
      providerHosts.has(u.hostname)
    );
  } catch {
    return false;
  }
}
export function retryDelay(value: string | null, now = Date.now()): number | undefined {
  if (!value) return;
  const n = /^\d+$/.test(value) ? Number(value) * 1000 : Date.parse(value) - now;
  return Number.isFinite(n) && n > 0 ? Math.min(n, 86400000) : undefined;
}
/** All provider requests are bounded JSON reads. No replay download path exists here. */
export const providerJson: JsonTransport = async (request) => {
  if (!allowedProviderUrl(request.url)) return { status: 'invalid_response' };
  try {
    const response = await fetch(request.url, {
      headers: {
        'User-Agent': 'CommunityGames/0.19 (+https://github.com/ddk220-light/NammaPUBobot)',
        Accept: 'application/json',
        ...request.headers,
      },
      signal: AbortSignal.timeout(8000),
      redirect: 'manual',
    });
    if (!response.ok) {
      await response.body?.cancel();
      const status =
        response.status === 404
          ? 'not_found'
          : [401, 403].includes(response.status)
            ? 'authentication_required'
            : response.status === 429
              ? 'throttled'
              : 'unavailable';
      return {
        status,
        ...(status === 'throttled' || response.headers.has('Retry-After')
          ? { retryAfterMs: retryDelay(response.headers.get('Retry-After')) ?? 120000 }
          : {}),
      };
    }
    const limit = Math.min(4000000, Math.max(1024, request.maxBytes ?? 1000000));
    const reader = response.body?.getReader();
    if (!reader) return { status: 'invalid_response' };
    let length = 0;
    const parts: Uint8Array[] = [];
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        length += value.byteLength;
        if (length > limit) return { status: 'invalid_response' };
        parts.push(value);
      }
    } finally {
      await reader.cancel().catch(() => {});
    }
    const bytes = new Uint8Array(length);
    let offset = 0;
    for (const part of parts) {
      bytes.set(part, offset);
      offset += part.length;
    }
    try {
      return {
        status: 'ok',
        value: parseProviderJson(
          new TextDecoder('utf-8', { fatal: true, ignoreBOM: false }).decode(bytes),
        ),
      };
    } catch {
      return { status: 'invalid_response' };
    }
  } catch {
    return { status: 'unavailable' };
  }
};
export const unsupported = <T>(): ProviderResult<T> => ({ status: 'unsupported' });

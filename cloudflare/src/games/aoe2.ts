import type { GameAdapter, LobbyObservation, LobbyPlayer, ProviderResult } from './types';
const api = 'https://data.aoe2companion.com/api';
const headers = { 'User-Agent': 'CommunityGames/0.9 (github.com/ddk220-light/NammaPUBobot)' };
const identifier = (value: unknown): string | null => {
  const s = String(value ?? '');
  return /^[1-9]\d{0,11}$/.test(s) ? s : null;
};
const text = (value: unknown, fallback = '') =>
  typeof value === 'string' ? value.slice(0, 100) : fallback;
function timestamp(value: unknown, now: number): number | null {
  if (value === null || value === undefined) return null;
  if (
    typeof value !== 'string' ||
    !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,9})?(?:Z|[+-]\d\d:\d\d)$/.test(value)
  )
    throw Error('Invalid provider timestamp');
  const t = Date.parse(value);
  if (!Number.isFinite(t) || t < 946684800000 || t > now) throw Error('Invalid provider timestamp');
  return t;
}
function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value))
    throw Error('Invalid provider response');
  return value as Record<string, unknown>;
}
function player(value: unknown, fallbackTeam?: unknown): LobbyPlayer | null {
  const p = record(value),
    id = identifier(p.profileId);
  if (!id) throw Error('Invalid player identity');
  return {
    ...(text(p.civName) ? { selection: text(p.civName) } : {}),
    profileId: id,
    name: text(p.name, `Player ${id}`),
    team: p.team == null && fallbackTeam == null ? null : String(p.team ?? fallbackTeam),
  };
}
export function parseAoE2Match(value: unknown, id: string, now = Date.now()): LobbyObservation {
  const m = record(value);
  if (m.success === false || identifier(m.matchId) !== id || !Array.isArray(m.teams))
    throw Error('Match identity mismatch');
  const startedAt = timestamp(m.started, now),
    finishedAt = timestamp(m.finished, now);
  if (finishedAt && (!startedAt || finishedAt < startedAt)) throw Error('Inconsistent match times');
  const players = m.teams.flatMap((value) => {
    const t = record(value);
    if (!Array.isArray(t.players)) throw Error('Missing team');
    return t.players.map((p) => player(p, t.teamId)).filter((p): p is LobbyPlayer => p !== null);
  });
  if (players.length > 16 || new Set(players.map((p) => p.profileId)).size !== players.length)
    throw Error('Invalid roster');
  return {
    externalId: id,
    name: text(m.name, 'AoE2 lobby'),
    phase: finishedAt ? 'finished' : startedAt ? 'started' : 'open',
    observedAt: now,
    startedAt,
    finishedAt,
    players,
    capacity: null,
    map: text(m.mapName) || null,
    source: 'aoe2companion-match',
  };
}
async function json(path: string): Promise<ProviderResult<unknown>> {
  try {
    const response = await fetch(`${api}${path}`, {
      headers,
      signal: AbortSignal.timeout(6000),
      redirect: 'error',
    });
    if (response.status === 404) return { status: 'not_found' };
    if (!response.ok) {
      const retry = response.headers.get('Retry-After');
      const delay = retry
        ? /^\d+$/.test(retry)
          ? Number(retry) * 1000
          : Date.parse(retry) - Date.now()
        : NaN;
      return {
        status: 'unavailable',
        retryAfterMs:
          Number.isFinite(delay) && delay > 0
            ? Math.min(delay, 86400000)
            : response.status === 429
              ? 120000
              : undefined,
      };
    }
    const reader = response.body?.getReader();
    if (!reader) return { status: 'unavailable' };
    let bytes = 0;
    const parts: Uint8Array[] = [];
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        bytes += value.byteLength;
        if (bytes > 400000) throw Error('Response too large');
        parts.push(value);
      }
    } finally {
      await reader.cancel().catch(() => {});
    }
    const merged = new Uint8Array(bytes);
    let offset = 0;
    for (const part of parts) {
      merged.set(part, offset);
      offset += part.length;
    }
    return { status: 'ok', value: JSON.parse(new TextDecoder().decode(merged)) };
  } catch {
    return { status: 'unavailable' };
  }
}
export const aoe2Adapter: GameAdapter = {
  id: 'aoe2',
  profileProvider: 'aoe2',
  parseLobbyReference(input) {
    if (identifier(input.trim())) return input.trim();
    try {
      const u = new URL(input.trim());
      if (
        u.protocol === 'aoe2de:' &&
        u.hostname === '0' &&
        !u.username &&
        !u.password &&
        !u.port &&
        identifier(u.pathname.slice(1))
      )
        return u.pathname.slice(1);
      if (
        u.protocol === 'https:' &&
        !u.username &&
        !u.password &&
        !u.port &&
        [
          'aoe2companion.com',
          'www.aoe2companion.com',
          'aoe2insights.com',
          'www.aoe2insights.com',
          'aoe.ms',
        ].includes(u.hostname)
      ) {
        const match = /^\/(?:match|matches|lobby|join)\/([1-9]\d{0,11})\/?$/.exec(u.pathname);
        const id = match?.[1] ?? (u.pathname === '/lobby' ? u.searchParams.get('gameId') : null);
        if (id && identifier(id)) return id;
      }
    } catch {}
    throw Error('Enter the AoE2 lobby ID or a supported lobby link.');
  },
  async profile(input) {
    const id = identifier(input);
    if (!id) return { status: 'not_found' };
    const result = await json(`/profiles/${id}`);
    if (result.status !== 'ok') return result;
    try {
      const p = record(result.value);
      if (identifier(p.profileId) !== id || p.success === false) throw Error('Wrong profile');
      return {
        status: 'ok',
        value: {
          id,
          name: text(p.name, `Player ${id}`),
          url: `https://www.aoe2companion.com/profile/${id}`,
        },
      };
    } catch {
      return { status: 'unavailable' };
    }
  },
  async observe(id) {
    if (!identifier(id)) return { status: 'not_found' };
    const result = await json(`/matches/${id}`);
    if (result.status !== 'ok') return result;
    try {
      const value = parseAoE2Match(result.value, id);
      // This HTTP endpoint does not establish live occupancy before a game starts.
      return value.startedAt ? { status: 'ok', value } : { status: 'not_found' };
    } catch {
      return { status: 'unavailable' };
    }
  },
  profileUrl(id) {
    if (!identifier(id)) throw Error('Invalid profile');
    return `https://www.aoe2companion.com/profile/${id}`;
  },
  lobbyUrl(id) {
    if (!identifier(id)) throw Error('Invalid lobby');
    return `https://aoe.ms/lobby?gameId=${id}`;
  },
  joinUri(id) {
    if (!identifier(id)) throw Error('Invalid lobby');
    return `aoe2de://0/${id}`;
  },
};

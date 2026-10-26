import type { GameAdapter, LobbyObservation, LobbyPlayer, ProviderResult } from './types';
const api = 'https://data.aoe2companion.com/api';
const headers = { 'User-Agent': 'CommunityGames/0.9 (github.com/ddk220-light/NammaPUBobot)' };
const identifier = (value: unknown): string | null => {
  const s = String(value ?? '');
  return /^[1-9]\d{0,11}$/.test(s) ? s : null;
};
const text = (value: unknown, fallback = '') =>
  typeof value === 'string' ? value.slice(0, 100) : fallback;
function timestamp(value: unknown): number | null {
  if (value === null || value === undefined) return null;
  if (typeof value !== 'string' || !/^\d{4}-\d\d-\d\dT/.test(value))
    throw Error('Invalid provider timestamp');
  const t = Date.parse(value);
  if (!Number.isFinite(t) || t < 946684800000 || t > Date.now() + 300000)
    throw Error('Invalid provider timestamp');
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
  if (!id) return null;
  return {
    profileId: id,
    name: text(p.name, `Player ${id}`),
    team: p.team == null && fallbackTeam == null ? null : String(p.team ?? fallbackTeam),
  };
}
export function parseAoE2Match(value: unknown, id: string, now = Date.now()): LobbyObservation {
  const m = record(value);
  if (identifier(m.matchId) !== id || !Array.isArray(m.teams))
    throw Error('Match identity mismatch');
  const startedAt = timestamp(m.started),
    finishedAt = timestamp(m.finished);
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
/** Initial filtered socket frames contain a lobby and its full slot snapshot. Deltas alone are not enough. */
export function parseAoE2Lobby(
  value: unknown,
  id: string,
  now = Date.now(),
): LobbyObservation | null {
  if (!Array.isArray(value) || value.length > 100) throw Error('Invalid lobby snapshot');
  const events = value.map(record);
  const found = events.find(
    (e) => e.type === 'lobbyAdded' && identifier(record(e.data).matchId) === id,
  );
  if (!found) return null;
  const m = record(found.data);
  const total = m.totalSlotCount,
    blocked = m.blockedSlotCount ?? 0;
  if (
    !Number.isInteger(total) ||
    !Number.isInteger(blocked) ||
    Number(total) < 1 ||
    Number(total) > 16 ||
    Number(blocked) < 0 ||
    Number(blocked) > Number(total)
  )
    throw Error('Invalid lobby capacity');
  const slots = events
    .filter((e) => e.type === 'slotAdded' && identifier(record(e.data).matchId) === id)
    .map((e) => record(e.data));
  // A partial frame cannot prove who is missing or that the lobby is full.
  if (slots.length < Number(total)) return null;
  const players = slots
    .filter((s) => s.status === 'player')
    .map((s) => player(s))
    .filter((p): p is LobbyPlayer => p !== null);
  if (new Set(players.map((p) => p.profileId)).size !== players.length)
    throw Error('Duplicate lobby player');
  return {
    externalId: id,
    name: text(m.name, 'AoE2 lobby'),
    phase: players.length >= Number(total) - Number(blocked) ? 'full' : 'open',
    observedAt: now,
    startedAt: null,
    finishedAt: null,
    players,
    capacity: Number(total) - Number(blocked),
    map: text(m.mapName) || null,
    source: 'aoe2companion-lobby-snapshot',
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
    if (!response.ok)
      return { status: 'unavailable', retryAfterMs: response.status === 429 ? 120000 : undefined };
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
async function lobbySnapshot(id: string): Promise<ProviderResult<LobbyObservation>> {
  let ws: WebSocket | undefined;
  try {
    const response = await fetch(
      `https://socket.aoe2companion.com/listen?handler=lobbies&match_ids=${id}`,
      {
        headers: { ...headers, Upgrade: 'websocket' },
        signal: AbortSignal.timeout(4000),
        redirect: 'error',
      },
    );
    ws = response.webSocket ?? undefined;
    if (!ws) return { status: 'unavailable' };
    const socket = ws;
    // Short snapshot only: close after one complete initial frame. No idle permanent connection.
    return await new Promise((resolve) => {
      let done = false;
      const finish = (result: ProviderResult<LobbyObservation>) => {
        if (done) return;
        done = true;
        clearTimeout(timer);
        socket.close(1000, 'Snapshot complete');
        resolve(result);
      };
      const timer = setTimeout(() => finish({ status: 'not_found' }), 1500);
      socket.addEventListener('message', (event) => {
        try {
          if (typeof event.data !== 'string' || event.data.length > 100000)
            return finish({ status: 'unavailable' });
          const observation = parseAoE2Lobby(JSON.parse(event.data), id);
          if (observation) finish({ status: 'ok', value: observation });
        } catch {
          finish({ status: 'unavailable' });
        }
      });
      socket.addEventListener('error', () => finish({ status: 'unavailable' }));
      socket.addEventListener('close', () => finish({ status: 'unavailable' }));
      socket.accept();
    });
  } catch {
    return { status: 'unavailable' };
  } finally {
    try {
      ws?.close(1000, 'Snapshot complete');
    } catch {}
  }
}
export const aoe2Adapter: GameAdapter = {
  id: 'aoe2',
  profileProvider: 'aoe2',
  parseLobbyReference(input) {
    if (identifier(input.trim())) return input.trim();
    try {
      const u = new URL(input.trim());
      if (u.protocol === 'aoe2de:' && u.hostname === '0' && identifier(u.pathname.slice(1)))
        return u.pathname.slice(1);
      if (
        u.protocol === 'https:' &&
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
  async observe(id, started) {
    if (!identifier(id)) return { status: 'not_found' };
    const result = await json(`/matches/${id}`);
    if (result.status === 'ok') {
      try {
        const value = parseAoE2Match(result.value, id);
        if (value.startedAt) return { status: 'ok', value };
      } catch {
        return { status: 'unavailable' };
      }
    }
    if (started || (result.status === 'unavailable' && result.retryAfterMs))
      return result.status === 'ok' ? { status: 'unavailable' } : result;
    return lobbySnapshot(id);
  },
  joinUri(id) {
    if (!identifier(id)) throw Error('Invalid lobby');
    return `aoe2de://0/${id}`;
  },
};

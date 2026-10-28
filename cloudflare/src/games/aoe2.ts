import type { AdapterRuntime, GameAdapter, LobbyObservation, LobbyPlayer } from './types';
import { isSteamIndividualId } from './steam';
import { providerJson } from './transport';
import { gameCapabilities } from './catalog';
const api = 'https://data.aoe2companion.com/api';
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
    ...(p.civ != null ? { selectionId: String(p.civ) } : {}),
    ...(typeof p.won === 'boolean'
      ? { outcome: p.won ? ('win' as const) : ('loss' as const) }
      : {}),
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
    schemaVersion: 2,
    edition: gameCapabilities('aoe2')!.manifest.edition,
    provider: 'aoe2companion',
    accountNamespace: 'aoe2:profile',
    nativeLobbyId: id,
    nativeMatchId: id,
    confidence: 'provisional',
    ...(startedAt !== null && finishedAt !== null
      ? { elapsedDuration: { value: (finishedAt - startedAt) / 1000, unit: 'seconds' as const } }
      : {}),
    replayReferences: m.teams.flatMap((value) =>
      (record(value).players as unknown[]).map((entry) => {
        const p = record(entry),
          perspectiveId = identifier(p.profileId)!;
        return {
          provider: 'aoe2companion',
          matchId: id,
          perspectiveId,
          availability:
            p.replay === true
              ? ('available' as const)
              : p.replay === false
                ? ('unavailable' as const)
                : ('unknown' as const),
          ...(p.replay === true
            ? { sourceUrl: `https://aoe.ms/replay/?gameId=${id}&profileId=${perspectiveId}` }
            : {}),
        };
      }),
    ),
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
export function createAoE2Adapter(runtime: AdapterRuntime = {}): GameAdapter {
  const request = runtime.request ?? providerJson;
  const json = (path: string) =>
    request({ url: `${api}${path}`, maxBytes: path.startsWith('/profiles/') ? 2000000 : 400000 });
  const adapter: GameAdapter = {
    id: 'aoe2',
    manifest: gameCapabilities('aoe2')!.manifest,
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
            namespace: 'aoe2:profile',
            edition: gameCapabilities('aoe2')!.manifest.edition,
            ...(isSteamIndividualId(p.steamId) ? { platformId: p.steamId } : {}),
          },
        };
      } catch {
        return { status: 'invalid_response' };
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
        return { status: 'invalid_response' };
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
  adapter.match = (id) => adapter.observe(id, true);
  adapter.replays = async (id) => {
    const result = await adapter.match!(id);
    return result.status === 'ok'
      ? { status: 'ok', value: result.value.replayReferences ?? [] }
      : result;
  };
  adapter.history = async (profileId) => {
    if (!identifier(profileId)) return { status: 'not_found' };
    const result = await json(`/matches?profile_ids=${encodeURIComponent(profileId)}`);
    if (result.status !== 'ok') return result;
    try {
      const body = record(result.value);
      if (body.success === false || !Array.isArray(body.matches))
        return { status: 'invalid_response' };
      const value = body.matches
        .map((m) => parseAoE2Match(m, identifier(record(m).matchId) ?? ''))
        .sort((a, b) => (b.startedAt ?? 0) - (a.startedAt ?? 0));
      return { status: 'ok', value };
    } catch {
      return { status: 'invalid_response' };
    }
  };
  return adapter;
}
export const aoe2Adapter = createAoE2Adapter();

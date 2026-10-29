import { gameCapabilities } from './catalog';
import { providerJson } from './transport';
import type {
  AdapterRuntime,
  GameAdapter,
  LobbyObservation,
  LobbyPlayer,
  ProviderResult,
  ReplayReference,
} from './types';

const api = 'https://api.playgenerals.online/env/prod/contract/1';
const namespace = 'generalsonline:user';
const edition = 'Command & Conquer Generals: Zero Hour';
const identifier = (v: unknown): string | null => {
  if (typeof v === 'number' && !Number.isSafeInteger(v)) return null;
  return /^[1-9]\d{0,18}$/.test(String(v)) ? String(v) : null;
};
const object = (v: unknown): Record<string, unknown> => {
  if (!v || typeof v !== 'object' || Array.isArray(v))
    throw Error('Invalid GeneralsOnline response');
  return v as Record<string, unknown>;
};
const array = (v: unknown): unknown[] => {
  if (!Array.isArray(v)) throw Error('Missing GeneralsOnline collection');
  return v;
};
const text = (v: unknown, fallback = '') => (typeof v === 'string' ? v.slice(0, 100) : fallback);
const flag = (v: unknown): boolean | undefined =>
  v === true || v === 1 ? true : v === false || v === 0 ? false : undefined;
function instant(v: unknown, now: number): number {
  // Production timestamp timezone is an acceptance gate; do not silently interpret server-local time as UTC.
  if (typeof v !== 'string' || !/T.*(?:Z|[+-]\d\d:\d\d)$/.test(v))
    throw Error('Unknown timestamp timezone');
  const t = Date.parse(v);
  if (!Number.isFinite(t) || t < 946684800000 || t > now)
    throw Error('Invalid GeneralsOnline timestamp');
  return t;
}
const team = (v: unknown): string | null =>
  typeof v === 'number' && Number.isInteger(v) && v >= 0 && v <= 7 ? `team:${v}` : null;
function selection(v: unknown) {
  // Side numbering depends on the provider's patch. Preserve unknown IDs instead of guessing the wrong army.
  return typeof v === 'number' && Number.isInteger(v) && v >= 0
    ? { selectionId: String(v), selection: `Faction ${v}` }
    : {};
}
function roster(players: LobbyPlayer[]) {
  if (players.length > 8 || new Set(players.map((p) => p.profileId)).size !== players.length)
    throw Error('Invalid GeneralsOnline roster');
  return players;
}
function checkEnvelope(value: unknown) {
  const v = object(value);
  if (
    v.success === false ||
    v.error ||
    v.errors ||
    (typeof v.status === 'number' && v.status >= 400)
  )
    throw Error('Provider reported failure');
  return v;
}

/** Wire contract from Services 85dfbc7ac6f9e336f5d18939f3cd1eb20663b6ea; production credentials still required. */
export function parseGeneralsLobby(value: unknown, now = Date.now()): LobbyObservation {
  const l = object(value),
    id = identifier(l.LobbyID);
  if (!id || typeof l.State !== 'number' || ![0, 1, 2].includes(l.State))
    throw Error('Invalid GeneralsOnline lobby identity/state');
  const members = array(l.Members).map(object);
  const players = roster(
    members
      .filter((p) => p.SlotState === 5 && p.Side !== 1)
      .map((p) => {
        const profileId = identifier(p.UserID);
        if (!profileId) throw Error('Missing GeneralsOnline native account ID');
        return {
          profileId,
          name: text(p.DisplayName, `Player ${profileId}`),
          team: team(p.Team),
          accountNamespace: namespace,
          ...selection(p.Side),
        };
      }),
  );
  const capacity =
    typeof l.MaxPlayers === 'number' &&
    Number.isInteger(l.MaxPlayers) &&
    l.MaxPlayers >= 1 &&
    l.MaxPlayers <= 8
      ? l.MaxPlayers
      : null;
  const matchId = identifier(l.MatchID);
  return {
    schemaVersion: 2,
    provider: 'generalsonline',
    edition,
    accountNamespace: namespace,
    externalId: id,
    nativeLobbyId: id,
    ...(matchId ? { nativeMatchId: matchId } : {}),
    name: text(l.Name, 'GeneralsOnline lobby'),
    phase:
      l.State === 2
        ? 'finished'
        : l.State === 1
          ? 'started'
          : capacity !== null && players.length === capacity
            ? 'full'
            : 'open',
    observedAt: now,
    startedAt: null,
    finishedAt: null,
    players,
    capacity,
    map: text(l.MapName) || null,
    source: 'generalsonline-lobbies',
    confidence: 'provisional',
    visibility:
      l.IsPassworded === true ? 'private' : l.IsPassworded === false ? 'public' : 'unknown',
    ...(l.ExeCRC !== undefined || l.IniCRC !== undefined
      ? { gameBuild: `exe:${text(String(l.ExeCRC ?? '?'))};ini:${text(String(l.IniCRC ?? '?'))}` }
      : {}),
  };
}

export function parseGeneralsMatch(
  value: unknown,
  expectedId?: string,
  now = Date.now(),
): LobbyObservation {
  const m = object(value),
    id = identifier(m.match_id);
  if (!id || (expectedId && id !== expectedId) || typeof m.is_finished !== 'boolean')
    throw Error('GeneralsOnline match identity mismatch');
  const startedAt = instant(m.time_started, now),
    finishedAt = m.is_finished ? instant(m.time_finished, now) : null;
  if (finishedAt !== null && finishedAt < startedAt)
    throw Error('Inconsistent GeneralsOnline times');
  const members = array(m.members)
    .filter((v) => v !== null)
    .map(object);
  const humans = members.filter((p) => p.slot_state === 5 && p.side !== 1);
  const hasBots = members.some((p) => [2, 3, 4].includes(Number(p.slot_state)));
  const sides = new Map<string, Set<boolean>>();
  for (const p of humans) {
    const side = team(p.team),
      won = flag(p.won);
    if (side && won !== undefined) {
      const values = sides.get(side) ?? new Set<boolean>();
      values.add(won);
      sides.set(side, values);
    }
  }
  const conclusive =
    m.is_finished &&
    !hasBots &&
    humans.length >= 2 &&
    humans.every(
      (p) => team(p.team) !== null && flag(p.won) !== undefined && flag(p.desynced) === false,
    ) &&
    sides.size === 2 &&
    [...sides.values()].every((s) => s.size === 1) &&
    [...sides.values()].filter((s) => s.has(true)).length === 1;
  const players = roster(
    humans.map((p) => {
      const profileId = identifier(p.user_id);
      if (!profileId) throw Error('Missing GeneralsOnline account ID');
      return {
        profileId,
        name: text(p.display_name, `Player ${profileId}`),
        team: team(p.team),
        accountNamespace: namespace,
        ...selection(p.side),
        outcome: conclusive
          ? ((flag(p.won) ? 'win' : 'loss') as 'win' | 'loss')
          : ('unknown' as const),
      };
    }),
  );
  if (!players.length) throw Error('Missing GeneralsOnline participants');
  const replayReferences: ReplayReference[] = humans.map((p) => {
    const files = p.metadata === undefined ? [] : array(p.metadata).map(object);
    return {
      provider: 'generalsonline',
      matchId: id,
      perspectiveId: identifier(p.user_id)!,
      availability: files.some(
        (f) => f.file_type === 1 && typeof f.file_name === 'string' && /\.rep$/i.test(f.file_name),
      )
        ? 'available'
        : m.is_finished
          ? 'unknown'
          : 'unavailable',
      // Link to the provider's public match page; do not reconstruct storage bucket URLs.
      sourceUrl: `https://strata.gamereplays.org/zh/match/${id}`,
    };
  });
  return {
    schemaVersion: 2,
    provider: 'generalsonline',
    edition,
    accountNamespace: namespace,
    externalId: `match:${id}`,
    nativeMatchId: id,
    name: text(m.lobby_name, 'GeneralsOnline match'),
    phase: m.is_finished ? 'finished' : 'started',
    observedAt: now,
    startedAt,
    finishedAt,
    players,
    capacity: players.length,
    map: text(m.map_name) || null,
    source: 'generalsonline-match-history',
    confidence: 'provisional',
    ...(finishedAt !== null
      ? { elapsedDuration: { value: (finishedAt - startedAt) / 1000, unit: 'seconds' as const } }
      : {}),
    // Per-member duration units have not been validated against an authorized production response.
    replayReferences,
    visibility: 'unknown',
    mode: hasBots ? 'bots' : text(m.match_roster_type) || undefined,
  };
}
function parseReference(input: string) {
  const v = input.trim();
  if (identifier(v)) return v;
  if (/^match:[1-9]\d{0,18}$/.test(v)) return v;
  try {
    const u = new URL(v);
    if (
      u.protocol === 'https:' &&
      u.hostname === 'strata.gamereplays.org' &&
      !u.username &&
      !u.password &&
      !u.port
    ) {
      const match = /^\/zh\/match\/([1-9]\d{0,18})\/?$/.exec(u.pathname);
      if (match) return `match:${match[1]}`;
    }
  } catch {}
  throw Error('Enter the GeneralsOnline lobby ID or a Zero Hour match-page link.');
}

export function createGeneralsOnlineAdapter(runtime: AdapterRuntime = {}): GameAdapter {
  const transport = runtime.request ?? providerJson;
  const request = async (path: string): Promise<ProviderResult<unknown>> => {
    if (!runtime.generalsOnlineApiKey) return { status: 'authentication_required' };
    return transport({
      url: `${api}${path}`,
      headers: { 'X-Api-Key': runtime.generalsOnlineApiKey },
      cacheTtlMs: path === '/Lobbies' ? 30000 : 120000,
      maxBytes: 2000000,
    });
  };
  const discover = async (): Promise<ProviderResult<LobbyObservation[]>> => {
    const r = await request('/Lobbies');
    if (r.status !== 'ok') return r;
    try {
      const lobbies = array(checkEnvelope(r.value).lobbies);
      if (lobbies.length > 2000) throw Error('GeneralsOnline list too large');
      return { status: 'ok', value: lobbies.map((l) => parseGeneralsLobby(l)) };
    } catch {
      return { status: 'invalid_response' };
    }
  };
  const match = async (input: string): Promise<ProviderResult<LobbyObservation>> => {
    const id = identifier(input);
    if (!id) return { status: 'not_found' };
    const r = await request(`/MatchHistory/${id}`);
    if (r.status !== 'ok') return r;
    try {
      const rows = array(object(checkEnvelope(r.value).matches).matches);
      if (rows.length > 100) throw Error('GeneralsOnline range exceeded');
      const found = rows.filter((m) => identifier(object(m).match_id) === id);
      if (found.length > 1) throw Error('Duplicate GeneralsOnline match');
      return found.length
        ? { status: 'ok', value: parseGeneralsMatch(found[0], id) }
        : { status: 'delayed', retryAfterMs: 120000 };
    } catch {
      return { status: 'invalid_response' };
    }
  };
  return {
    id: 'zero_hour',
    manifest: gameCapabilities('zero_hour')!.manifest,
    profileProvider: 'generalsonline',
    parseLobbyReference: parseReference,
    parseAccountReference(input) {
      const id = identifier(input.trim());
      if (!id) throw Error('Enter the native GeneralsOnline user ID, not a Steam or Discord ID.');
      return id;
    },
    async profile() {
      return { status: 'unsupported' };
    },
    discover,
    match,
    async observe(id, _started, context) {
      if (id.startsWith('match:')) return match(id.slice(6));
      if (!identifier(id)) return { status: 'not_found' };
      const pin = context?.nativeMatchId ?? context?.previous?.nativeMatchId;
      // Once the native match ID is pinned, a same-room rematch must not replace it.
      if (pin) {
        const pinned = await match(pin);
        if (pinned.status === 'ok')
          return { status: 'ok', value: { ...pinned.value, externalId: id, nativeLobbyId: id } };
        if (pinned.status !== 'delayed' && pinned.status !== 'not_found') return pinned;
      }
      const list = await discover();
      if (list.status !== 'ok') return list;
      const current = list.value.find((o) => o.nativeLobbyId === id);
      if (pin && current?.nativeMatchId && pin !== current.nativeMatchId)
        return { status: 'delayed', retryAfterMs: 120000 };
      const matchId = pin ?? current?.nativeMatchId;
      if (matchId) {
        const r = await match(matchId);
        if (r.status === 'ok')
          return { status: 'ok', value: { ...r.value, externalId: id, nativeLobbyId: id } };
        // Propagate provider failures; publication delay can still leave a valid live observation.
        if (r.status !== 'delayed' && r.status !== 'not_found') return r;
        if (current) return { status: 'ok', value: current };
        return r;
      }
      return current
        ? { status: 'ok', value: current }
        : { status: 'delayed', retryAfterMs: 120000 };
    },
    async replays(id) {
      const r = await match(id);
      return r.status === 'ok' ? { status: 'ok', value: r.value.replayReferences ?? [] } : r;
    },
    profileUrl(id) {
      if (!identifier(id)) throw Error('Invalid GeneralsOnline account ID');
      return 'https://www.playgenerals.online/';
    },
    lobbyUrl(id) {
      const ref = parseReference(id);
      return ref.startsWith('match:')
        ? `https://strata.gamereplays.org/zh/match/${ref.slice(6)}`
        : 'https://www.playgenerals.online/lobbies';
    },
    joinUri() {
      return '';
    },
  };
}

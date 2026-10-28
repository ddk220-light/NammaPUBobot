import { gameCapabilities } from './catalog';
import { providerJson } from './transport';
import type {
  AdapterRuntime,
  GameAdapter,
  LobbyObservation,
  LobbyPlayer,
  ProviderProfile,
  ProviderResult,
  ReplayReference,
} from './types';

const api = 'https://api.bar-rts.com';
const namespace = 'bar:user';
const edition = 'Beyond All Reason';
const numericId = (v: unknown): string | null => {
  if (typeof v === 'number' && !Number.isSafeInteger(v)) return null;
  return /^(?:0|[1-9]\d{0,19})$/.test(String(v)) ? String(v) : null;
};
const replayId = (v: unknown): string | null =>
  typeof v === 'string' && /^[a-f0-9]{32}$/.test(v) ? v : null;
const label = (v: unknown, fallback = '') => (typeof v === 'string' ? v.slice(0, 100) : fallback);
const object = (v: unknown): Record<string, unknown> => {
  if (!v || typeof v !== 'object' || Array.isArray(v)) throw Error('Invalid BAR response');
  return v as Record<string, unknown>;
};
const array = (v: unknown): unknown[] => {
  if (!Array.isArray(v)) throw Error('Invalid BAR collection');
  return v;
};
function roster(players: LobbyPlayer[]) {
  if (players.length > 16 || new Set(players.map((p) => p.profileId)).size !== players.length)
    throw Error('Unsupported or duplicate BAR roster');
  return players;
}
function instant(v: unknown, now: number): number {
  if (typeof v !== 'string' || !/T.*(?:Z|[+-]\d\d:\d\d)$/.test(v))
    throw Error('Missing BAR timestamp');
  const t = Date.parse(v);
  if (!Number.isFinite(t) || t < 946684800000 || t > now) throw Error('Invalid BAR timestamp');
  return t;
}
function milliseconds(v: unknown): number | undefined {
  if (v === undefined || v === null) return;
  if (typeof v !== 'number' || !Number.isFinite(v) || v < 0 || v > 7 * 86400000)
    throw Error('Invalid BAR duration');
  return v;
}
function replayReference(m: Record<string, unknown>, now: number): ReplayReference {
  const id = replayId(m.id);
  if (!id) throw Error('Missing BAR replay hash');
  const available = typeof m.fileName === 'string' && /\.sdfz$/i.test(m.fileName);
  // The provider warns that files older than a year may expire. This is not a storage guarantee.
  const old = typeof m.startTime === 'string' && now - Date.parse(m.startTime) > 365 * 86400000;
  return {
    provider: 'bar',
    matchId: id,
    availability: available ? (old ? 'unknown' : 'available') : 'unknown',
    sourceUrl: `https://bar-rts.com/replays/${id}`,
  };
}

/** The live feed's teamId is an engine/player team, not a proven ally-team assignment. */
export function parseBarBattle(value: unknown, now = Date.now()): LobbyObservation {
  const b = object(value),
    id = numericId(b.battleId);
  if (id === null) throw Error('Invalid BAR battle ID');
  const members = array(b.players).map(object);
  const players = roster(
    members
      .filter((p) => {
        if (p.status && object(p.status).bot === true) return false;
        if (p.gameStatus === 'Spectating' || p.joinedGameAfterStart === true) return false;
        // Missing team means the public feed cannot distinguish this member from a spectator.
        return numericId(p.teamId) !== null;
      })
      .map((p) => {
        const profileId = numericId(p.userId);
        if (!profileId) throw Error('Missing BAR user ID');
        return {
          profileId,
          name: label(p.username, `Player ${profileId}`),
          team: null,
          accountNamespace: namespace,
        };
      }),
  );
  const capacity =
    typeof b.maxPlayers === 'number' &&
    Number.isInteger(b.maxPlayers) &&
    b.maxPlayers > 0 &&
    b.maxPlayers <= 16
      ? b.maxPlayers
      : null;
  const running = b.lobbyStatus === 'running';
  if (b.lobbyStatus !== undefined && !['waiting', 'running'].includes(String(b.lobbyStatus)))
    throw Error('Unrecognized BAR lobby state');
  return {
    schemaVersion: 2,
    externalId: id,
    nativeLobbyId: id,
    provider: 'bar',
    edition,
    accountNamespace: namespace,
    name: label(b.title, 'BAR battle'),
    phase: running ? 'started' : capacity !== null && players.length === capacity ? 'full' : 'open',
    observedAt: now,
    startedAt: null,
    finishedAt: null,
    players,
    capacity,
    map: label(b.map) || null,
    source: 'bar-battles',
    confidence: 'provisional',
    gameBuild: label(b.game) || undefined,
    visibility: b.passworded === true ? 'private' : b.passworded === false ? 'public' : 'unknown',
    mode: label(b.gameType) || undefined,
  };
}

/** Decode API-produced replay metadata only. No replay binary is fetched or parsed by this bot. */
export function parseBarReplay(
  value: unknown,
  expectedId?: string,
  now = Date.now(),
): LobbyObservation {
  const m = object(value),
    hash = replayId(m.id);
  if (!hash || (expectedId && hash !== expectedId)) throw Error('BAR replay identity mismatch');
  const startedAt = instant(m.startTime, now),
    simulation = milliseconds(m.durationMs),
    elapsed = milliseconds(m.fullDurationMs);
  // Upstream demo-parser defines fullDurationMs from header.wallclockTime and durationMs from gameDuration.
  const finishedAt = elapsed !== undefined ? startedAt + elapsed : null;
  if (finishedAt !== null && finishedAt > now) throw Error('Future BAR completion');
  const teams = array(m.AllyTeams).map(object);
  const hasBots = m.hasBots === true || teams.some((t) => array(t.AIs).length > 0);
  const conclusive =
    m.gameEndedNormally === true &&
    !hasBots &&
    teams.length === 2 &&
    teams.every((t) => typeof t.winningTeam === 'boolean') &&
    teams.filter((t) => t.winningTeam === true).length === 1;
  const teamIds = teams.map((t) => numericId(t.allyTeamId));
  if (teamIds.some((t) => t === null) || new Set(teamIds).size !== teamIds.length)
    throw Error('Invalid BAR ally teams');
  const players = roster(
    teams.flatMap((t, i) =>
      array(t.Players).map((value) => {
        const p = object(value),
          profileId = numericId(p.userId);
        if (!profileId) throw Error('Missing BAR user ID');
        return {
          profileId,
          name: label(p.name, `Player ${profileId}`),
          team: `ally:${teamIds[i]}`,
          accountNamespace: namespace,
          ...(label(p.faction)
            ? { selection: label(p.faction), selectionId: label(p.faction) }
            : {}),
          outcome: conclusive
            ? ((t.winningTeam ? 'win' : 'loss') as 'win' | 'loss')
            : ('unknown' as const),
        };
      }),
    ),
  );
  if (!players.length) throw Error('Missing BAR participants');
  const host = object(m.hostSettings),
    serverMatchId = numericId(host.server_match_id);
  return {
    schemaVersion: 2,
    externalId: hash,
    ...(serverMatchId ? { nativeMatchId: serverMatchId } : {}),
    edition,
    provider: 'bar',
    accountNamespace: namespace,
    name: 'BAR replay summary',
    phase: 'finished',
    observedAt: now,
    startedAt,
    finishedAt,
    players,
    capacity: players.length,
    map: label(m.Map ? object(m.Map).scriptName : host.mapname) || null,
    source: 'bar-replay-summary',
    confidence: 'provisional',
    gameBuild: label(m.gameVersion) || undefined,
    ...(simulation !== undefined
      ? { simulationDuration: { value: simulation / 1000, unit: 'seconds' as const } }
      : {}),
    ...(elapsed !== undefined
      ? { elapsedDuration: { value: elapsed / 1000, unit: 'seconds' as const } }
      : {}),
    replayReferences: [replayReference(m, now)],
    visibility: 'public',
    mode: hasBots ? 'bots' : label(m.preset) || undefined,
  };
}

function parseReference(input: string) {
  const v = input.trim();
  if (numericId(v) !== null || replayId(v)) return v;
  try {
    const u = new URL(v);
    if (u.protocol === 'https:' && !u.username && !u.password && !u.port) {
      if (u.hostname === 'bar-rts.com' || u.hostname === 'api.bar-rts.com') {
        const match = /^\/replays\/([a-f0-9]{32})\/?$/.exec(u.pathname);
        if (match) return match[1];
      }
      if (
        ['www.beyondallreason.info', 'beyondallreason.info'].includes(u.hostname) &&
        u.pathname === '/replays' &&
        replayId(u.searchParams.get('gameId'))
      )
        return u.searchParams.get('gameId')!;
    }
  } catch {}
  throw Error('Enter a BAR battle ID or a provider replay-page link/hash.');
}

export function createBarAdapter(runtime: AdapterRuntime = {}): GameAdapter {
  const request = runtime.request ?? providerJson;
  const rawBattles = async (): Promise<ProviderResult<Record<string, unknown>[]>> => {
    const r = await request({ url: `${api}/battles`, cacheTtlMs: 30000, maxBytes: 2000000 });
    if (r.status !== 'ok') return r;
    try {
      const values = array(r.value);
      if (values.length > 2000) throw Error('BAR list too large');
      return { status: 'ok', value: values.map(object) };
    } catch {
      return { status: 'invalid_response' };
    }
  };
  const profile = async (input: string): Promise<ProviderResult<ProviderProfile>> => {
    const id = numericId(input);
    if (!id) return { status: 'not_found' };
    const r = await rawBattles();
    if (r.status !== 'ok') return r;
    try {
      for (const b of r.value)
        for (const v of array(b.players)) {
          const p = object(v);
          if (numericId(p.userId) === id)
            return {
              status: 'ok',
              value: {
                id,
                name: label(p.username, `Player ${id}`),
                namespace,
                edition,
                url: `https://bar-rts.com/players/${id}`,
              },
            };
        }
      // /cached-users is a 13+ MB global directory. Absence from the bounded live roster is not absence of an account.
      return { status: 'delayed', retryAfterMs: 120000 };
    } catch {
      return { status: 'invalid_response' };
    }
  };
  const match = async (id: string): Promise<ProviderResult<LobbyObservation>> => {
    if (!replayId(id)) return { status: 'not_found' };
    const r = await request({ url: `${api}/replays/${id}`, cacheTtlMs: 120000, maxBytes: 1000000 });
    if (r.status !== 'ok') return r;
    try {
      return { status: 'ok', value: parseBarReplay(r.value, id) };
    } catch {
      return { status: 'invalid_response' };
    }
  };
  const history = async (profileId: string): Promise<ProviderResult<LobbyObservation[]>> => {
    const p = await profile(profileId);
    if (p.status !== 'ok') return p;
    const u = new URL(`${api}/replays`);
    u.searchParams.set('page', '1');
    u.searchParams.set('limit', '5');
    // A name finds candidates only; numeric user IDs from each detail response establish membership.
    u.searchParams.set('players', p.value.name);
    const r = await request({ url: u.href, cacheTtlMs: 120000, maxBytes: 200000 });
    if (r.status !== 'ok') return r;
    try {
      const rows = array(object(r.value).data);
      if (rows.length > 5) throw Error('BAR history limit ignored');
      const candidates: LobbyObservation[] = [];
      for (const row of rows) {
        const hash = replayId(object(row).id);
        if (!hash) throw Error('Invalid BAR replay list');
        const detail = await match(hash);
        if (detail.status !== 'ok') return detail;
        if (detail.value.players.some((p) => p.profileId === profileId))
          candidates.push(detail.value);
      }
      return {
        status: 'ok',
        value: candidates.sort((a, b) => (b.startedAt ?? 0) - (a.startedAt ?? 0)),
      };
    } catch {
      return { status: 'invalid_response' };
    }
  };
  return {
    id: 'bar',
    manifest: gameCapabilities('bar')!.manifest,
    profileProvider: 'bar',
    parseLobbyReference: parseReference,
    parseAccountReference(input) {
      const id = numericId(input.trim());
      if (!id) throw Error('Enter the numeric BAR user ID.');
      return id;
    },
    profile,
    match,
    history,
    async discover() {
      const r = await rawBattles();
      if (r.status !== 'ok') return r;
      try {
        // Large rooms remain visible at the provider. This bot supports at most 16 players and two teams.
        const supported = r.value.filter(
          (b) => typeof b.maxPlayers === 'number' && b.maxPlayers <= 16,
        );
        return { status: 'ok', value: supported.map((b) => parseBarBattle(b)) };
      } catch {
        return { status: 'invalid_response' };
      }
    },
    async observe(id, _started, context) {
      if (replayId(id)) return match(id);
      if (numericId(id) === null) return { status: 'not_found' };
      const pinned = context?.previous?.replayReferences?.find(
        (r) => r.provider === 'bar' && replayId(r.matchId),
      );
      if (pinned) {
        const r = await match(pinned.matchId);
        if (r.status !== 'ok') return r;
        if (context?.nativeMatchId && r.value.nativeMatchId !== context.nativeMatchId)
          return { status: 'invalid_response' };
        return { status: 'ok', value: { ...r.value, externalId: id, nativeLobbyId: id } };
      }
      const r = await rawBattles();
      if (r.status !== 'ok') return r;
      try {
        const b = r.value.find((b) => numericId(b.battleId) === id);
        if (!b) return { status: 'delayed', retryAfterMs: 120000 };
        const value = parseBarBattle(b);
        // A reused room cannot establish which replay belongs to this binding. Never guess from identical rosters.
        if (context?.previous?.phase === 'started' && value.phase !== 'started')
          return { status: 'delayed', retryAfterMs: 120000 };
        return { status: 'ok', value };
      } catch {
        return { status: 'invalid_response' };
      }
    },
    async replays(id) {
      const r = await match(id);
      return r.status === 'ok' ? { status: 'ok', value: r.value.replayReferences ?? [] } : r;
    },
    profileUrl(id) {
      if (!numericId(id)) throw Error('Invalid BAR user ID');
      return `https://bar-rts.com/players/${id}`;
    },
    lobbyUrl(id) {
      const ref = parseReference(id);
      return replayId(ref) ? `https://bar-rts.com/replays/${ref}` : 'https://bar-rts.com/battles';
    },
    joinUri() {
      return '';
    },
  };
}

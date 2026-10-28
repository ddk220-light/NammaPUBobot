import { isSteamIndividualId } from './steam';
import { ageCatalogs } from './age-catalogs';
import { gameCapabilities } from './catalog';
import { providerJson } from './transport';
import type {
  AdapterRuntime,
  GameAdapter,
  JsonTransport,
  LobbyObservation,
  LobbyPlayer,
  ObserveContext,
  ProviderProfile,
  ProviderResult,
  ReplayReference,
} from './types';

export type AgeTitle = 'aoe3' | 'aoe4' | 'aom';
export interface AgeConfiguration {
  id: AgeTitle;
  title: 'age3' | 'age4' | 'athens';
  host: 'https://aoe-api.worldsedgelink.com' | 'https://athens-live-api.worldsedgelink.com';
  edition: string;
  selectionLabel: 'Civilization' | 'God';
  /** Only AoE4's documented gt counter is treated as simulation seconds. */
  simulationCounter: boolean;
}
type Row = Record<string, unknown>;
function record(value: unknown): Row {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw Error('Invalid object');
  return value as Row;
}
function array(value: unknown, max = 500): unknown[] {
  if (!Array.isArray(value) || value.length > max) throw Error('Invalid array');
  return value;
}
export function ageIdentifier(value: unknown): string {
  if (typeof value === 'number' && !Number.isSafeInteger(value)) throw Error('Unsafe identifier');
  if (
    (typeof value !== 'string' && typeof value !== 'number') ||
    !/^[1-9]\d{0,39}$/.test(String(value))
  )
    throw Error('Invalid identifier');
  return String(value);
}
function integer(value: unknown): number | undefined {
  if (typeof value === 'number' && Number.isSafeInteger(value)) return value;
  if (typeof value === 'string' && /^-?\d{1,12}$/.test(value)) return Number(value);
}
function text(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value.slice(0, 100) : undefined;
}
function timestamp(value: unknown, now: number): number | null {
  if (value == null || value === 0 || value === '0') return null;
  const seconds = integer(value);
  if (seconds === undefined || seconds < 946684800 || seconds * 1000 > now)
    throw Error('Invalid timestamp');
  return seconds * 1000;
}
function team(value: unknown): string | null {
  const n = integer(value);
  return n !== undefined && n >= 0 && n <= 31 ? `we:${n}` : null;
}
function outcome(value: unknown): LobbyPlayer['outcome'] {
  return integer(value) === 1 ? 'win' : integer(value) === 0 ? 'loss' : 'unknown';
}
/** Application failures are not empty successes, even when HTTP returned 200. */
export function worldEdgeEnvelope(value: unknown): ProviderResult<Row> {
  try {
    const body = record(value),
      result = record(body.result),
      code = integer(result.code);
    if (code === undefined) return { status: 'invalid_response' };
    if (code === 0) return { status: 'ok', value: body };
    if (code === 2) return { status: 'not_found' };
    const message = String(result.message ?? '').toUpperCase();
    if (/THROTTL|RATE.?LIMIT/.test(message)) return { status: 'throttled', retryAfterMs: 120000 };
    if (/AUTH|PRIVATE|ACCESS.?DENIED/.test(message)) return { status: 'authentication_required' };
    return { status: 'unavailable' };
  } catch {
    return { status: 'invalid_response' };
  }
}
function names(value: unknown): Map<string, string> {
  return new Map(
    array(value ?? [], 10000).map((v) => {
      const p = record(v),
        id = ageIdentifier(p.profile_id);
      return [id, text(p.alias) ?? `Player ${id}`];
    }),
  );
}
function selection(
  p: Row,
  config: AgeConfiguration,
): Pick<LobbyPlayer, 'selection' | 'selectionId'> {
  const n = integer(p.civilization_id);
  if (n === undefined || n <= 0) return {};
  const id = String(n);
  return {
    selectionId: id,
    selection: ageCatalogs[config.id].selections[id] ?? `${config.selectionLabel} ${id} (unknown)`,
  };
}
function participant(
  p: Row,
  profiles: Map<string, string>,
  config: AgeConfiguration,
  finished: boolean,
): LobbyPlayer {
  const id = ageIdentifier(p.profile_id);
  return {
    profileId: id,
    name: profiles.get(id) ?? `Player ${id}`,
    team: team(p.teamid),
    accountNamespace: `worlds-edge:${config.title}`,
    ...selection(p, config),
    ...(finished ? { outcome: outcome(p.resulttype) } : {}),
  };
}
function roster(players: LobbyPlayer[]): LobbyPlayer[] {
  if (players.length > 16 || new Set(players.map((p) => p.profileId)).size !== players.length)
    throw Error('Invalid roster');
  return players;
}
function base(config: AgeConfiguration, now: number) {
  return {
    schemaVersion: 2 as const,
    edition: config.edition,
    provider: 'worlds-edge',
    accountNamespace: `worlds-edge:${config.title}`,
    source: `worlds-edge:${config.title}`,
    observedAt: now,
    confidence: 'provisional' as const,
  };
}
function replayUrl(value: unknown): string | undefined {
  if (typeof value !== 'string' || value.length > 4096) return;
  try {
    const u = new URL(value);
    if (u.protocol !== 'https:' || u.username || u.password || u.port) return;
    // Provider-provided references are displayed, never fetched by the bot.
    if (!u.hostname.endsWith('.blob.core.windows.net') && u.hostname !== 'api.ageofempires.com')
      return;
    return u.toString();
  } catch {
    return;
  }
}
function replayMetadata(
  rows: unknown[],
  config: AgeConfiguration,
  id: string,
  now: number,
  expiry?: number,
): ReplayReference[] {
  const seen = new Set<string>();
  return rows.flatMap((value) => {
    const r = record(value);
    if (integer(r.datatype) !== 0) return []; // summary JSON is not a recording
    if (r.matchhistory_id != null && ageIdentifier(r.matchhistory_id) !== id)
      throw Error('Wrong replay match');
    const perspectiveId = ageIdentifier(r.profile_id),
      key = `${id}:${perspectiveId}`;
    if (seen.has(key)) return [];
    seen.add(key);
    const size = integer(r.size),
      expired = expiry !== undefined && expiry <= now;
    const available = size !== undefined && size > 0;
    // AoE4's official service refreshes its delivery URL; no binary request occurs here.
    const sourceUrl =
      available && !expired
        ? config.id === 'aoe4'
          ? `https://api.ageofempires.com/api/GameStats/AgeIV/GetMatchReplay/?matchId=${id}&profileId=${perspectiveId}`
          : expiry !== undefined
            ? replayUrl(r.url)
            : undefined
        : undefined;
    return [
      {
        provider: `worlds-edge:${config.title}`,
        matchId: id,
        perspectiveId,
        availability: expired
          ? ('expired' as const)
          : available
            ? ('available' as const)
            : ('unknown' as const),
        ...(sourceUrl ? { sourceUrl } : {}),
        ...(expiry !== undefined ? { expiresAt: expiry } : {}),
      },
    ];
  });
}
export function decodeAgeLobby(
  value: unknown,
  profiles: Map<string, string>,
  config: AgeConfiguration,
  now = Date.now(),
): LobbyObservation {
  const m = record(value),
    id = ageIdentifier(m.id),
    capacity = integer(m.maxplayers);
  if (integer(m.state) !== 0) throw Error('Unknown advertisement state');
  const players = roster(
    array(m.matchmembers, 16).map((v) => participant(record(v), profiles, config, false)),
  );
  const platform =
    m.steamlobbyid && String(m.steamlobbyid) !== '0' ? ageIdentifier(m.steamlobbyid) : undefined;
  return {
    ...base(config, now),
    externalId: id,
    nativeLobbyId: id,
    ...(platform ? { platformLobbyId: platform } : {}),
    name: text(m.description) ?? `${config.edition} lobby`,
    phase: capacity && players.length === capacity ? 'full' : 'open',
    startedAt: null,
    finishedAt: null,
    players,
    capacity: capacity && capacity > 0 && capacity <= 16 ? capacity : null,
    map: text(m.mapname) ?? null,
    visibility:
      integer(m.visible) === 1 ? 'public' : integer(m.visible) === 0 ? 'private' : 'unknown',
    mode:
      ageCatalogs[config.id].modes[String(m.matchtype_id)] ??
      `Mode ${String(m.matchtype_id ?? 'unknown')}`,
  };
}
export function decodeAgeMatch(
  value: unknown,
  profiles: Map<string, string>,
  config: AgeConfiguration,
  now = Date.now(),
): LobbyObservation {
  const m = record(value),
    id = ageIdentifier(m.id);
  const startedAt = timestamp(m.startgametime, now),
    finishedAt = timestamp(m.completiontime, now);
  if (!startedAt || (finishedAt !== null && finishedAt < startedAt))
    throw Error('Invalid match times');
  const reports = array(m.matchhistoryreportresults ?? [], 16).map(record);
  const members = array(m.matchhistorymember ?? [], 16).map(record);
  const ids = new Set(reports.map((p) => ageIdentifier(p.profile_id)));
  if (
    ids.size !== reports.length ||
    new Set(members.map((p) => ageIdentifier(p.profile_id))).size !== members.length
  )
    throw Error('Duplicate reports');
  const players = roster([
    ...reports.map((p) => participant(p, profiles, config, finishedAt !== null)),
    ...members
      .filter((p) => !ids.has(ageIdentifier(p.profile_id)))
      .map((p) => participant(p, profiles, config, false)),
  ]);
  if (!players.length) throw Error('Missing match participants');
  for (const p of reports) {
    if (p.matchhistory_id != null && ageIdentifier(p.matchhistory_id) !== id)
      throw Error('Wrong participant match');
    const member = members.find((x) => ageIdentifier(x.profile_id) === ageIdentifier(p.profile_id));
    if (
      member &&
      ['win', 'loss'].includes(outcome(member.outcome)!) &&
      ['win', 'loss'].includes(outcome(p.resulttype)!) &&
      outcome(member.outcome) !== outcome(p.resulttype)
    ) {
      players.find((player) => player.profileId === ageIdentifier(p.profile_id))!.outcome =
        'unknown';
    }
  }
  // Report and membership collections may number the same teams differently.
  // Compare partitions, not numeric labels; conflicting grouping remains unknown.
  const represented = reports.map((p) => ({
    report: p,
    member: members.find((x) => ageIdentifier(x.profile_id) === ageIdentifier(p.profile_id)),
  }));
  for (const a of represented)
    for (const b of represented) {
      if (
        !a.member ||
        !b.member ||
        team(a.report.teamid) === null ||
        team(b.report.teamid) === null ||
        team(a.member.teamid) === null ||
        team(b.member.teamid) === null
      )
        continue;
      if (
        (team(a.report.teamid) === team(b.report.teamid)) !==
        (team(a.member.teamid) === team(b.member.teamid))
      ) {
        for (const id of [ageIdentifier(a.report.profile_id), ageIdentifier(b.report.profile_id)]) {
          const player = players.find((p) => p.profileId === id)!;
          player.team = null;
          player.outcome = 'unknown';
        }
      }
    }
  let simulationSeconds: number | undefined;
  if (config.simulationCounter && reports.length === players.length) {
    const counters = reports.map((p) => {
      try {
        return integer(record(JSON.parse(String(p.counters))).gt);
      } catch {
        return undefined;
      }
    });
    if (counters.length && counters.every((v) => v !== undefined && v >= 0 && v === counters[0]))
      simulationSeconds = counters[0];
  }
  const references = replayMetadata(array(m.matchurls ?? [], 64), config, id, now);
  return {
    ...base(config, now),
    externalId: id,
    nativeMatchId: id,
    name: text(m.description) ?? `${config.edition} match`,
    phase: finishedAt ? 'finished' : 'started',
    startedAt,
    finishedAt,
    players,
    capacity: null,
    map: text(m.mapname) ?? null,
    visibility: 'unknown',
    mode:
      ageCatalogs[config.id].modes[String(m.matchtype_id)] ??
      `Mode ${String(m.matchtype_id ?? 'unknown')}`,
    ...(finishedAt
      ? { elapsedDuration: { value: (finishedAt - startedAt) / 1000, unit: 'seconds' as const } }
      : {}),
    ...(simulationSeconds !== undefined
      ? { simulationDuration: { value: simulationSeconds, unit: 'seconds' as const } }
      : {}),
    ...(text(m.gameversion) ? { gameBuild: text(m.gameversion) } : {}),
    replayReferences: references.length
      ? references
      : [{ provider: `worlds-edge:${config.title}`, matchId: id, availability: 'unknown' }],
  };
}

export function createAgeAdapter(
  config: AgeConfiguration,
  runtime: AdapterRuntime = {},
): GameAdapter {
  const transport: JsonTransport = runtime.request ?? providerJson;
  const manifest = gameCapabilities(config.id)!.manifest;
  function url(path: string, params: Record<string, string> = {}) {
    const u = new URL(`/community/${path}`, config.host);
    u.searchParams.set('title', config.title);
    for (const [k, v] of Object.entries(params)) u.searchParams.set(k, v);
    return u.toString();
  }
  async function request(
    path: string,
    params: Record<string, string>,
    ttl = 30000,
  ): Promise<ProviderResult<Row>> {
    const response = await transport({
      url: url(path, params),
      cacheTtlMs: ttl,
      maxBytes: 4000000,
    });
    return response.status === 'ok' ? worldEdgeEnvelope(response.value) : response;
  }
  function profileUrl(id: string) {
    ageIdentifier(id);
    if (config.id === 'aoe4') return `https://aoe4world.com/players/${id}`;
    if (config.id === 'aom') return `https://aomstats.io/profile/${id}`;
    return url('leaderboard/getPersonalStat', { profile_ids: `[${id}]` });
  }
  async function profileLookup(
    input: string,
    steam: boolean,
  ): Promise<ProviderResult<ProviderProfile>> {
    let id: string;
    try {
      id = ageIdentifier(input);
      if (steam && !isSteamIndividualId(id)) return { status: 'not_found' };
    } catch {
      return { status: 'not_found' };
    }
    const response = await request(
      'leaderboard/getPersonalStat',
      steam ? { profile_names: JSON.stringify([`/steam/${id}`]) } : { profile_ids: `[${id}]` },
      300000,
    );
    if (response.status !== 'ok') return response;
    try {
      const members = array(response.value.statGroups, 100).flatMap((g) =>
        array(record(g).members, 16).map(record),
      );
      const matching = members.filter((p) =>
        steam ? p.name === `/steam/${id}` : ageIdentifier(p.profile_id) === id,
      );
      const unique = new Map(matching.map((p) => [ageIdentifier(p.profile_id), p]));
      if (!unique.size) return { status: 'not_found' };
      if (unique.size !== 1) return { status: 'invalid_response' };
      const [profileId, p] = [...unique.entries()][0];
      const candidateSteamId = /^\/steam\/(\d{17})$/.exec(String(p.name))?.[1];
      const platformId = isSteamIndividualId(candidateSteamId) ? candidateSteamId : undefined;
      return {
        status: 'ok',
        value: {
          id: profileId,
          name: text(p.alias) ?? `Player ${profileId}`,
          url: profileUrl(profileId),
          namespace: manifest.accountNamespace,
          edition: config.edition,
          ...(platformId ? { platformId } : {}),
        },
      };
    } catch {
      return { status: 'invalid_response' };
    }
  }
  async function matches(
    path: 'getMatchHistory' | 'getRecentMatchHistory',
    params: Record<string, string>,
  ): Promise<ProviderResult<LobbyObservation[]>> {
    const response = await request(`leaderboard/${path}`, params, 30000);
    if (response.status !== 'ok') return response;
    try {
      const people = names(response.value.profiles);
      const rows = array(
        response.value[path === 'getMatchHistory' ? 'matchHistory' : 'matchHistoryStats'],
      );
      const decoded = rows.map((m) => decodeAgeMatch(m, people, config));
      if (new Set(decoded.map((m) => m.nativeMatchId)).size !== decoded.length)
        return { status: 'invalid_response' };
      return {
        status: 'ok',
        value: decoded.sort(
          (a, b) =>
            (b.startedAt ?? 0) - (a.startedAt ?? 0) || a.externalId.localeCompare(b.externalId),
        ),
      };
    } catch {
      return { status: 'invalid_response' };
    }
  }
  async function match(input: string): Promise<ProviderResult<LobbyObservation>> {
    let id: string;
    try {
      id = ageIdentifier(input);
    } catch {
      return { status: 'not_found' };
    }
    const response = await matches('getMatchHistory', { matchIDs: `[${id}]` });
    if (response.status !== 'ok') return response;
    if (!response.value.length) return { status: 'not_found' };
    if (response.value.length !== 1 || response.value[0].nativeMatchId !== id)
      return { status: 'invalid_response' };
    return { status: 'ok', value: response.value[0] };
  }
  async function discover(): Promise<ProviderResult<LobbyObservation[]>> {
    const response = await request('advertisement/findAdvertisements', {});
    if (response.status !== 'ok') return response;
    try {
      const people = names(response.value.avatars);
      const rows = array(response.value.matches, 1000);
      return { status: 'ok', value: rows.map((m) => decodeAgeLobby(m, people, config)) };
    } catch {
      return { status: 'invalid_response' };
    }
  }
  async function observe(
    id: string,
    _started: boolean,
    context: ObserveContext = {},
  ): Promise<ProviderResult<LobbyObservation>> {
    try {
      ageIdentifier(id);
    } catch {
      return { status: 'not_found' };
    }
    if (!context.nativeMatchId && !context.previous?.startedAt) {
      const publicLobbies = await discover();
      if (publicLobbies.status === 'ok') {
        const lobby = publicLobbies.value.find((m) => m.nativeLobbyId === id);
        if (lobby) return { status: 'ok', value: lobby };
      } else if (publicLobbies.status !== 'not_found') return publicLobbies;
    }
    const expected = [
      ...new Set(
        context.profileIds?.length
          ? context.profileIds
          : (context.previous?.players.map((p) => p.profileId) ?? []),
      ),
    ].sort();
    if (expected.length < 2 || expected.length > 16 || !context.attachedAt)
      return { status: 'delayed', retryAfterMs: 120000 };
    // A previously observed open lobby cannot belong to a result that already started.
    // One second allows for the publisher's whole-second timestamp precision.
    const latestOpen =
      context.previous && ['open', 'full'].includes(context.previous.phase)
        ? context.previous.observedAt - 1000
        : 0;
    const earliestHistoryStart = Math.max(context.attachedAt - 120000, latestOpen);
    // An explicitly supplied native ID may be attached after the game has started.
    // It must still belong to this bot match; heuristic history matches retain the tighter window.
    const earliestDirectStart = Math.max(
      (context.matchCreatedAt ?? context.attachedAt) - 120000,
      latestOpen,
    );
    const fits = (m: LobbyObservation, earliestStart: number) =>
      !!m.startedAt &&
      m.startedAt >= earliestStart &&
      JSON.stringify(m.players.map((p) => p.profileId).sort()) === JSON.stringify(expected);
    const bound = (m: LobbyObservation): ProviderResult<LobbyObservation> => ({
      status: 'ok',
      value: {
        ...m,
        externalId: id,
        nativeLobbyId: id,
        confidence: 'roster',
        ...(context.previous?.platformLobbyId
          ? { platformLobbyId: context.previous.platformLobbyId }
          : {}),
      },
    });
    const direct = await match(context.nativeMatchId ?? id);
    if (direct.status === 'ok' && fits(direct.value, earliestDirectStart))
      return bound(direct.value);
    if (context.nativeMatchId)
      return direct.status === 'ok' ? { status: 'invalid_response' } : direct;
    if (direct.status !== 'ok' && direct.status !== 'not_found') return direct;
    const history = await matches('getRecentMatchHistory', {
      profile_ids: `[${expected.slice(0, 2).map(ageIdentifier).join(',')}]`,
    });
    if (history.status !== 'ok')
      return history.status === 'not_found' ? { status: 'delayed', retryAfterMs: 120000 } : history;
    const candidates = history.value.filter((m) => fits(m, earliestHistoryStart));
    return candidates.length === 1
      ? bound(candidates[0])
      : { status: 'delayed', retryAfterMs: 120000 };
  }
  return {
    id: config.id,
    manifest,
    profileProvider: config.id,
    parseLobbyReference(input) {
      const s = input.trim();
      try {
        return ageIdentifier(s);
      } catch {}
      throw Error(`Enter the ${config.edition} native lobby ID (not its Steam lobby ID).`);
    },
    parseAccountReference: ageIdentifier,
    profile: (id) => profileLookup(id, false),
    resolveSteam: (id) => profileLookup(id, true),
    profileUrl,
    discover,
    match,
    observe,
    async history(id) {
      try {
        ageIdentifier(id);
      } catch {
        return { status: 'not_found' };
      }
      return matches('getRecentMatchHistory', { profile_ids: `[${id}]` });
    },
    async replays(id) {
      try {
        ageIdentifier(id);
      } catch {
        return { status: 'not_found' };
      }
      const response = await request('leaderboard/getReplayFiles', { matchIDs: `[${id}]` }, 0);
      if (response.status === 'not_found')
        return {
          status: 'ok',
          value: [
            { provider: `worlds-edge:${config.title}`, matchId: id, availability: 'unavailable' },
          ],
        };
      if (response.status !== 'ok') return response;
      try {
        const expirySeconds = integer(response.value.expiryUnix);
        const expiry =
          expirySeconds !== undefined && expirySeconds > 0 ? expirySeconds * 1000 : undefined;
        const references = replayMetadata(
          array(response.value.replayFiles, 64),
          config,
          id,
          Date.now(),
          expiry,
        );
        return {
          status: 'ok',
          value: references.length
            ? references
            : [
                {
                  provider: `worlds-edge:${config.title}`,
                  matchId: id,
                  availability: 'unavailable',
                },
              ],
        };
      } catch {
        return { status: 'invalid_response' };
      }
    },
    lobbyUrl() {
      return '';
    },
    joinUri() {
      return '';
    },
  };
}

import { gameCapabilities } from './catalog';
import { providerJson } from './transport';
import { wc3RaceSelection, wc3SupportedModes } from './wc3-catalog';
import type {
  AdapterRuntime,
  GameAdapter,
  LobbyObservation,
  LobbyPlayer,
  ObserveContext,
  ProviderProfile,
  ProviderResult,
} from './types';

export const wc3AccountNamespace = 'w3champions:battletag';
export const wc3Edition = 'Warcraft III / W3Champions matchmaking';
const api = 'https://website-backend.w3champions.com/api';
const website = 'https://w3champions.com';
const pageSize = 50;
const maxPages = 2;
const historySize = 20;
const recordIdPattern = /^[a-f0-9]{24}$/;
const originalIdPattern = /^[A-Za-z0-9_-]{1,64}$/;

function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value))
    throw Error('Invalid W3Champions object');
  return value as Record<string, unknown>;
}
function array(value: unknown, limit: number): unknown[] {
  if (!Array.isArray(value) || value.length > limit) throw Error('Invalid W3Champions array');
  return value;
}
function label(value: unknown): string {
  if (
    typeof value !== 'string' ||
    !value.trim() ||
    value.length > 100 ||
    /[\u0000-\u001f\u007f]/.test(value)
  )
    throw Error('Invalid W3Champions label');
  return value;
}
function originalId(value: unknown): string {
  if (typeof value !== 'string' || !originalIdPattern.test(value))
    throw Error('Invalid W3Champions matchmaking ID');
  return value;
}
function numericId(value: unknown): string {
  if (typeof value === 'number' && Number.isSafeInteger(value) && value > 0) return String(value);
  if (typeof value === 'string' && /^[1-9]\d{0,39}$/.test(value)) return value;
  throw Error('Invalid W3Champions numeric ID');
}
function websiteUrl(value: string): URL {
  const url = new URL(value);
  if (
    url.protocol !== 'https:' ||
    !['w3champions.com', 'www.w3champions.com'].includes(url.hostname) ||
    url.username ||
    url.password ||
    url.port ||
    url.search ||
    url.hash
  )
    throw Error('Use a W3Champions HTTPS link without query parameters.');
  return url;
}

/** The namespace is separate; the ID is the complete, case-preserved BattleTag. */
export function parseWc3AccountReference(input: string): string {
  let value = input.trim();
  if (value.startsWith('https://')) {
    const match = /^\/player\/([^/]+)\/?$/.exec(websiteUrl(value).pathname);
    if (!match) throw Error('Use a W3Champions player link or full BattleTag.');
    value = decodeURIComponent(match[1]);
  }
  if (!/^[\p{L}\p{N}][\p{L}\p{N}\p{M}]{1,31}#[1-9]\d{3,11}$/u.test(value))
    throw Error('Use the full BattleTag, including # and its numeric discriminator.');
  return value;
}
export function wc3ProfileUrl(id: string): string {
  return `${website}/player/${encodeURIComponent(parseWc3AccountReference(id))}`;
}

/** Record IDs are aliases, not interchangeable with original matchmaking or FLO IDs. */
export function parseWc3MatchReference(input: string): string {
  let value = input.trim();
  if (value.startsWith('https://')) {
    const match = /^\/match\/([a-f0-9]{24})\/?$/.exec(websiteUrl(value).pathname);
    if (!match) throw Error('Use a W3Champions match-page link or original matchmaking ID.');
    return `record:${match[1]}`;
  }
  if (value.startsWith('record:')) {
    if (!recordIdPattern.test(value.slice(7))) throw Error('Invalid W3Champions record ID');
    return value;
  }
  if (recordIdPattern.test(value)) return `record:${value}`;
  return originalId(value);
}

function instant(value: unknown, now: number): number {
  if (
    typeof value !== 'string' ||
    !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,7})?(?:Z|[+-]\d\d:\d\d)$/.test(value)
  )
    throw Error('Invalid W3Champions timestamp');
  const [year, month, day] = value.slice(0, 10).split('-').map(Number);
  if (month < 1 || month > 12 || day < 1 || day > new Date(Date.UTC(year, month, 0)).getUTCDate())
    throw Error('Invalid W3Champions calendar date');
  const time = Date.parse(value);
  if (!Number.isFinite(time) || time < 946684800000 || time > now)
    throw Error('Invalid W3Champions timestamp');
  return time;
}
class UnsupportedMode extends Error {}
function modeOf(row: Record<string, unknown>): string {
  if (typeof row.gameMode !== 'number' || !Number.isSafeInteger(row.gameMode) || row.gameMode < 0)
    throw Error('Invalid W3Champions mode');
  return String(row.gameMode);
}
function decodingFailure(error: unknown): ProviderResult<never> {
  return { status: error instanceof UnsupportedMode ? 'unsupported' : 'invalid_response' };
}
function applicationError(value: unknown): ProviderResult<never> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const row = value as Record<string, unknown>;
  const code = row.status ?? row.statusCode ?? row.code;
  if (code === 401 || code === 403) return { status: 'authentication_required' };
  if (code === 404) return { status: 'not_found' };
  if (code === 429) return { status: 'throttled' };
  if (row.error || row.errors || (code !== undefined && code !== 200))
    return { status: 'invalid_response' };
  return null;
}

export function decodeWc3Profile(value: unknown, expected: string): ProviderProfile {
  const row = object(value);
  const id = parseWc3AccountReference(expected);
  if (row.battleTag !== id) throw Error('W3Champions profile identity mismatch');
  return {
    id,
    name: label(row.name),
    url: wc3ProfileUrl(id),
    namespace: wc3AccountNamespace,
    edition: wc3Edition,
  };
}

/** The phase comes from the endpoint, never from ongoing result placeholders. */
export function decodeWc3Match(
  value: unknown,
  phase: 'started' | 'finished',
  now = Date.now(),
): LobbyObservation {
  const row = object(value);
  const mode = modeOf(row);
  const supported = wc3SupportedModes[mode];
  if (!supported) throw new UnsupportedMode('Unsupported W3Champions mode');
  const nativeMatchId = originalId(row['original-ongoing-match-id']);
  if (typeof row.id !== 'string' || !recordIdPattern.test(row.id))
    throw Error('Missing W3Champions record ID');
  const providerIds: Record<string, string> = {
    originalMatchId: nativeMatchId,
    recordId: row.id,
    [phase === 'started' ? 'ongoingRecordId' : 'finishedRecordId']: row.id,
  };
  for (const field of ['floGameId', 'floMatchId'] as const)
    if (row[field] !== undefined && row[field] !== null && row[field] !== 0 && row[field] !== '0')
      providerIds[field] = numericId(row[field]);
  if (
    providerIds.floGameId &&
    providerIds.floMatchId &&
    providerIds.floGameId !== providerIds.floMatchId
  )
    throw Error('Conflicting FLO identifiers');
  const startedAt = instant(row.startTime, now);
  const finishedAt = phase === 'finished' ? instant(row.endTime, now) : null;
  if (finishedAt !== null && finishedAt < startedAt) throw Error('Invalid W3Champions end time');
  if (
    phase === 'started' &&
    row.endTime !== null &&
    row.endTime !== undefined &&
    (typeof row.endTime !== 'string' ||
      !/^0001-01-01T00:00:00(?:\.0+)?(?:Z|\+00:00)$/.test(row.endTime))
  )
    throw Error('Ongoing payload contains a completion time');
  const teams = array(row.teams, 2).map(object);
  if (teams.length !== 2) throw Error('Incomplete W3Champions teams');
  const members = teams.map((team) => array(team.players, supported.teamSize).map(object));
  if (members.some((players) => players.length !== supported.teamSize))
    throw Error('Incomplete W3Champions roster');
  const conclusive =
    phase === 'finished' &&
    teams.every((team) => typeof team.won === 'boolean') &&
    teams.filter((team) => team.won === true).length === 1 &&
    members.every((players, index) => players.every((player) => player.won === teams[index].won));
  const players: LobbyPlayer[] = members.flatMap((members, index) =>
    members.map((player) => {
      if (typeof player.battleTag !== 'string') throw Error('Missing W3Champions player');
      const profileId = parseWc3AccountReference(player.battleTag);
      if (profileId !== player.battleTag) throw Error('Noncanonical W3Champions player');
      return {
        profileId,
        name: label(player.name),
        // The shared binding contract reserves 0 for unresolved teams.
        team: String(index + 1),
        accountNamespace: wc3AccountNamespace,
        ...wc3RaceSelection(player.race, player.rndRace),
        outcome: conclusive ? (teams[index].won ? 'win' : 'loss') : 'unknown',
      };
    }),
  );
  if (new Set(players.map((player) => player.profileId.toLowerCase())).size !== players.length)
    throw Error('Duplicate W3Champions player');
  let duration: number | undefined;
  if (phase === 'finished') {
    if (
      typeof row.durationInSeconds !== 'number' ||
      !Number.isSafeInteger(row.durationInSeconds) ||
      row.durationInSeconds < 0 ||
      row.durationInSeconds > 7 * 86400 ||
      Math.abs(row.durationInSeconds * 1000 - (finishedAt! - startedAt)) > 2000
    )
      throw Error('Invalid W3Champions completed duration');
    duration = row.durationInSeconds;
  }
  return {
    schemaVersion: 2,
    referenceKind: 'match',
    externalId: nativeMatchId,
    nativeMatchId,
    providerIds,
    provider: 'w3champions',
    edition: wc3Edition,
    accountNamespace: wc3AccountNamespace,
    name: `W3Champions ${supported.name}`,
    phase,
    observedAt: now,
    startedAt,
    finishedAt,
    players,
    capacity: players.length,
    map: label(row.mapName),
    mode,
    source: phase === 'started' ? 'w3champions-ongoing' : 'w3champions-finished',
    confidence: 'provisional',
    visibility: 'public',
    ...(duration !== undefined ? { elapsedDuration: { value: duration, unit: 'seconds' } } : {}),
    ...(phase === 'finished'
      ? {
          replayReferences: [
            {
              provider: 'w3champions',
              matchId: nativeMatchId,
              availability: 'unknown',
              sourceUrl: `${website}/match/${row.id}`,
            },
          ],
        }
      : {}),
  };
}

function collection(value: unknown, limit: number) {
  const root = object(value);
  const rows = array(root.matches, limit).map(object);
  if (
    typeof root.count !== 'number' ||
    !Number.isSafeInteger(root.count) ||
    root.count < rows.length
  )
    throw Error('Invalid W3Champions match count');
  return { rows, count: root.count };
}
function teamSignature(observation: LobbyObservation): string {
  const teams = new Map<string, string[]>();
  for (const player of observation.players) {
    if (player.team === null) throw Error('Unresolved W3Champions team');
    const group = teams.get(player.team) ?? [];
    group.push(player.profileId);
    teams.set(player.team, group);
  }
  return JSON.stringify([...teams.values()].map((team) => team.sort().join('\0')).sort());
}

export function createWc3Adapter(runtime: AdapterRuntime = {}): GameAdapter {
  const request = runtime.request ?? providerJson;
  async function get(path: string, cacheTtlMs = 30000): Promise<ProviderResult<unknown>> {
    const result = await request({ url: `${api}${path}`, cacheTtlMs, maxBytes: 1500000 });
    if (result.status !== 'ok') return result;
    return applicationError(result.value) ?? result;
  }
  async function rawProfile(id: string): Promise<ProviderResult<Record<string, unknown>>> {
    const result = await get(`/players/${encodeURIComponent(id)}`, 300000);
    if (result.status !== 'ok') return result;
    if (result.value === null) return { status: 'not_found' };
    try {
      decodeWc3Profile(result.value, id);
      return { status: 'ok', value: object(result.value) };
    } catch (error) {
      return decodingFailure(error);
    }
  }
  async function ongoingRows(): Promise<ProviderResult<Record<string, unknown>[]>> {
    const rows: Record<string, unknown>[] = [];
    for (let page = 0; page < maxPages; page++) {
      const result = await get(`/matches/ongoing?pageSize=${pageSize}&offset=${page * pageSize}`);
      if (result.status !== 'ok') return result;
      try {
        const data = collection(result.value, pageSize);
        for (const row of data.rows) {
          modeOf(row);
          originalId(row['original-ongoing-match-id']);
          if (typeof row.id !== 'string' || !recordIdPattern.test(row.id))
            throw Error('Invalid W3Champions record ID');
        }
        rows.push(...data.rows);
        if (data.rows.length < pageSize || rows.length >= data.count) break;
      } catch (error) {
        return decodingFailure(error);
      }
    }
    return { status: 'ok', value: rows };
  }
  async function match(input: string): Promise<ProviderResult<LobbyObservation>> {
    let reference: string;
    try {
      reference = parseWc3MatchReference(input);
    } catch {
      return { status: 'not_found' };
    }
    const isRecord = reference.startsWith('record:');
    const id = isRecord ? reference.slice(7) : reference;
    const result = await get(
      isRecord ? `/matches/${id}` : `/matches/by-ongoing-match-id/${id}`,
      120000,
    );
    if (result.status !== 'ok') return result;
    if (result.value === null) return { status: 'not_found' };
    try {
      const root = object(result.value);
      if (root.match === null) return { status: 'not_found' };
      const value = decodeWc3Match(root.match, 'finished');
      if ((isRecord ? value.providerIds!.recordId : value.nativeMatchId) !== id)
        throw Error('W3Champions match identity mismatch');
      return { status: 'ok', value };
    } catch (error) {
      return decodingFailure(error);
    }
  }
  function bound(
    value: LobbyObservation,
    reference: string,
    context: ObserveContext,
  ): ProviderResult<LobbyObservation> {
    try {
      const expected = context.profileIds?.length
        ? context.profileIds.map(parseWc3AccountReference)
        : context.previous?.players.map((player) => player.profileId);
      if (
        expected &&
        (new Set(expected).size !== expected.length ||
          JSON.stringify([...expected].sort()) !==
            JSON.stringify(value.players.map((p) => p.profileId).sort()))
      )
        throw Error('W3Champions bound roster mismatch');
      const earliest = context.matchCreatedAt ?? context.attachedAt;
      if (
        earliest !== undefined &&
        (!Number.isSafeInteger(earliest) || value.startedAt! < earliest - 120000)
      )
        throw Error('W3Champions match predates the binding');
      if (
        context.previous &&
        (context.previous.nativeMatchId !== value.nativeMatchId ||
          (context.previous.phase === 'finished' && value.phase !== 'finished') ||
          ['floGameId', 'floMatchId'].some(
            (key) =>
              context.previous?.providerIds?.[key] &&
              value.providerIds?.[key] &&
              context.previous.providerIds[key] !== value.providerIds[key],
          ) ||
          context.previous.mode !== value.mode ||
          teamSignature(context.previous) !== teamSignature(value) ||
          Math.abs((context.previous.startedAt ?? value.startedAt!) - value.startedAt!) > 1000)
      )
        throw Error('Conflicting W3Champions match continuity');
      return {
        status: 'ok',
        value: {
          ...value,
          externalId: reference,
          providerIds: { ...context.previous?.providerIds, ...value.providerIds },
          confidence: expected?.length ? 'roster' : 'provisional',
        },
      };
    } catch (error) {
      return decodingFailure(error);
    }
  }
  return {
    id: 'wc3',
    manifest: gameCapabilities('wc3')!.manifest,
    profileProvider: 'w3champions',
    parseAccountReference: parseWc3AccountReference,
    parseLobbyReference: parseWc3MatchReference,
    profileUrl: wc3ProfileUrl,
    lobbyUrl(input) {
      const reference = parseWc3MatchReference(input);
      return reference.startsWith('record:')
        ? `${website}/match/${reference.slice(7)}`
        : `${website}/matches`;
    },
    joinUri: () => '',
    async profile(input) {
      let id: string;
      try {
        id = parseWc3AccountReference(input);
      } catch {
        return { status: 'not_found' };
      }
      const result = await rawProfile(id);
      return result.status === 'ok'
        ? { status: 'ok', value: decodeWc3Profile(result.value, id) }
        : result;
    },
    async discoverOngoing() {
      const result = await ongoingRows();
      if (result.status !== 'ok') return result;
      try {
        const found = new Map<string, LobbyObservation>();
        for (const row of result.value) {
          if (!wc3SupportedModes[modeOf(row)]) continue;
          const observation = decodeWc3Match(row, 'started');
          found.set(observation.nativeMatchId!, observation);
        }
        return { status: 'ok', value: [...found.values()] };
      } catch (error) {
        return decodingFailure(error);
      }
    },
    match,
    async history(input) {
      let id: string;
      try {
        id = parseWc3AccountReference(input);
      } catch {
        return { status: 'not_found' };
      }
      const profile = await rawProfile(id);
      if (profile.status !== 'ok') return profile;
      try {
        const seasons = array(profile.value.participatedInSeasons, 1000).map((value) => {
          const season = object(value).id;
          if (typeof season !== 'number' || !Number.isSafeInteger(season) || season < 0)
            throw Error('Invalid W3Champions season');
          return season;
        });
        if (!seasons.length) return { status: 'ok', value: [] };
        const query = new URLSearchParams({
          playerId: id,
          season: String(Math.max(...seasons)),
          pageSize: String(historySize),
          offset: '0',
        });
        const result = await get(`/matches/search?${query}`, 120000);
        if (result.status !== 'ok') return result;
        const matches = collection(result.value, historySize)
          .rows.filter((row) => !!wc3SupportedModes[modeOf(row)])
          .map((row) => decodeWc3Match(row, 'finished'));
        if (
          matches.some((m) => !m.players.some((p) => p.profileId === id)) ||
          new Set(matches.map((m) => m.nativeMatchId)).size !== matches.length
        )
          throw Error('W3Champions history identity mismatch');
        return { status: 'ok', value: matches.sort((a, b) => b.startedAt! - a.startedAt!) };
      } catch (error) {
        return decodingFailure(error);
      }
    },
    async observe(input, _started, context = {}) {
      let reference: string;
      let pinned: string | undefined;
      try {
        reference = parseWc3MatchReference(input);
        const ids = [
          context.nativeMatchId,
          context.previous?.nativeMatchId,
          context.previous?.providerIds?.originalMatchId,
        ]
          .filter((id): id is string => id !== undefined)
          .map(originalId);
        if (new Set(ids).size > 1) throw Error('Conflicting W3Champions binding IDs');
        pinned = ids[0];
        if (pinned && !reference.startsWith('record:') && pinned !== reference)
          throw Error('W3Champions binding identity mismatch');
        if (context.previous && context.previous.externalId !== reference)
          throw Error('W3Champions reference changed');
      } catch {
        return { status: 'invalid_response' };
      }
      const recordAlias = reference.startsWith('record:');
      const provenAlias =
        recordAlias &&
        !!context.previous &&
        context.previous.nativeMatchId === pinned &&
        context.previous.providerIds?.originalMatchId === pinned &&
        ['recordId', 'ongoingRecordId', 'finishedRecordId'].some(
          (key) => context.previous?.providerIds?.[key] === reference.slice(7),
        );
      // A bare nativeMatchId hint cannot establish that an unrelated record alias belongs to it.
      const finished = await match(recordAlias && !provenAlias ? reference : (pinned ?? reference));
      if (finished.status === 'ok') {
        if (pinned && finished.value.nativeMatchId !== pinned)
          return { status: 'invalid_response' };
        return bound(finished.value, reference, context);
      }
      if (finished.status !== 'not_found') return finished;
      const expectedOriginal = pinned ?? (reference.startsWith('record:') ? undefined : reference);
      const isTarget = (row: Record<string, unknown>) =>
        (!expectedOriginal || row['original-ongoing-match-id'] === expectedOriginal) &&
        (!recordAlias || provenAlias || row.id === reference.slice(7));
      const playerId = context.profileIds?.[0] ?? context.previous?.players[0]?.profileId;
      let row: Record<string, unknown> | undefined;
      if (playerId) {
        let id: string;
        try {
          id = parseWc3AccountReference(playerId);
        } catch {
          return { status: 'invalid_response' };
        }
        const current = await get(`/matches/ongoing/${encodeURIComponent(id)}`);
        if (current.status !== 'ok' && current.status !== 'not_found') return current;
        if (current.status === 'ok' && current.value !== null) {
          try {
            row = object(current.value);
            modeOf(row);
            originalId(row['original-ongoing-match-id']);
            if (typeof row.id !== 'string' || !recordIdPattern.test(row.id))
              throw Error('Invalid W3Champions record ID');
          } catch {
            return { status: 'invalid_response' };
          }
        }
      } else {
        const current = await ongoingRows();
        if (current.status !== 'ok') return current;
        row = current.value.find(isTarget);
      }
      // A different current game (even with the identical roster) never replaces this reference.
      if (!row || !isTarget(row)) return { status: 'delayed', retryAfterMs: 120000 };
      try {
        return bound(decodeWc3Match(row, 'started'), reference, context);
      } catch (error) {
        return decodingFailure(error);
      }
    },
    async replays(id) {
      const result = await match(id);
      return result.status === 'ok'
        ? { status: 'ok', value: result.value.replayReferences ?? [] }
        : result;
    },
  };
}

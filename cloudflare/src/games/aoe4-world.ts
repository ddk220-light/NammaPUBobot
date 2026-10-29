import { providerJson } from './transport';
import { ageIdentifier } from './worlds-edge';
import type {
  AdapterRuntime,
  LobbyObservation,
  LobbyPlayer,
  ObserveContext,
  ProviderResult,
} from './types';

const origin = 'https://aoe4world.com';
const lookbackMs = 30 * 60 * 1000;
const delayed = (): ProviderResult<LobbyObservation> => ({
  status: 'delayed',
  retryAfterMs: 120000,
});
type Row = Record<string, unknown>;
function record(value: unknown): Row {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw Error('Invalid object');
  return value as Row;
}
function applicationObject(value: unknown): Row {
  const row = record(value);
  if (row.error != null || row.errors != null || row.status === 'error' || row.result != null)
    throw Error('Provider application error');
  return row;
}
function array(value: unknown, max: number): unknown[] {
  if (!Array.isArray(value) || value.length > max) throw Error('Invalid array');
  return value;
}
function text(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value.trim().slice(0, 100) : undefined;
}
function timestamp(value: unknown, now: number): number {
  if (typeof value !== 'string' || !/^\d{4}-\d\d-\d\dT/.test(value))
    throw Error('Invalid timestamp');
  const time = Date.parse(value);
  if (!Number.isFinite(time) || time < 946684800000 || time > now) throw Error('Invalid timestamp');
  return time;
}
export function worldReference(id: string): string {
  return `aoe4world:${ageIdentifier(id)}`;
}
export function worldReferenceId(input: string): string | undefined {
  const match = /^aoe4world:([1-9]\d{0,39})$/.exec(input);
  return match?.[1];
}
function profiles(players: LobbyPlayer[]): string {
  return JSON.stringify(players.map((p) => p.profileId).sort());
}
/** Compare partitions, since publisher and World team labels have separate namespaces. */
export function sameAoE4Match(a: LobbyObservation, b: LobbyObservation): boolean {
  if (
    !a.startedAt ||
    !b.startedAt ||
    Math.abs(a.startedAt - b.startedAt) > 1000 ||
    a.players.length < 2 ||
    profiles(a.players) !== profiles(b.players)
  )
    return false;
  return a.players.every((p) => {
    const corresponding = b.players.find((x) => x.profileId === p.profileId)!;
    return (
      p.team !== null &&
      corresponding.team !== null &&
      a.players.every((q) => {
        const other = b.players.find((x) => x.profileId === q.profileId)!;
        return (p.team === q.team) === (corresponding.team === other.team);
      })
    );
  });
}

/** Feed members wrap `player`; the player/game detail endpoint returns flattened members. */
export function decodeAoE4WorldGame(
  value: unknown,
  now = Date.now(),
): ProviderResult<LobbyObservation> {
  try {
    const game = applicationObject(value),
      id = ageIdentifier(game.game_id),
      startedAt = timestamp(game.started_at, now);
    if (typeof game.ongoing !== 'boolean') throw Error('Missing ongoing flag');
    if (game.updated_at != null && timestamp(game.updated_at, now) < startedAt)
      throw Error('Invalid update time');
    const mode = text(game.kind);
    if (!mode) throw Error('Missing game kind');
    const teams = array(game.teams, 16);
    if (teams.length < 2) throw Error('Missing teams');
    const players: LobbyPlayer[] = teams.flatMap((team, index) => {
      const members = array(team, 8);
      if (!members.length) throw Error('Empty team');
      return members.map((member) => {
        const entry = record(member),
          player = entry.player === undefined ? entry : record(entry.player),
          profileId = ageIdentifier(player.profile_id),
          civilization = text(player.civilization);
        if (player.civilization != null && !civilization) throw Error('Invalid civilization');
        const result = player.result;
        return {
          profileId,
          name: text(player.name) ?? `Player ${profileId}`,
          team: `aoe4world:${index}`,
          accountNamespace: 'worlds-edge:age4',
          ...(civilization
            ? {
                // Preserve new provider slugs; never guess a publisher civilization ID.
                selectionId: `aoe4world:${civilization}`,
                selection: civilization.replace(
                  /(^|[_-])([a-z])/g,
                  (_m, separator, letter) => `${separator ? ' ' : ''}${letter.toUpperCase()}`,
                ),
              }
            : {}),
          ...(!game.ongoing
            ? {
                outcome:
                  result === 'win' || result === 'loss' || result === 'draw' ? result : 'unknown',
              }
            : {}),
        } satisfies LobbyPlayer;
      });
    });
    if (
      players.length > 16 ||
      new Set(players.map((p) => p.profileId)).size !== players.length ||
      (mode === 'rm_1v1' && (teams.length !== 2 || players.length !== 2))
    )
      throw Error('Invalid roster');
    let finishedAt: number | null = null;
    if (!game.ongoing) {
      // `new_finished` can precede both duration and results by several minutes.
      if (game.duration == null) return delayed();
      if (
        typeof game.duration !== 'number' ||
        !Number.isSafeInteger(game.duration) ||
        game.duration < 0 ||
        game.duration > 604800
      )
        throw Error('Invalid duration');
      finishedAt = startedAt + game.duration * 1000;
      if (finishedAt > now) throw Error('Future completion');
    }
    const map =
      typeof game.map === 'string' || game.map == null
        ? text(game.map)
        : text(record(game.map).name);
    return {
      status: 'ok',
      value: {
        schemaVersion: 2,
        referenceKind: 'match',
        externalId: worldReference(id),
        providerIds: { aoe4world: id },
        edition: 'Age of Empires IV',
        provider: 'aoe4world',
        source: 'aoe4world',
        accountNamespace: 'worlds-edge:age4',
        name: 'Age of Empires IV match',
        phase: game.ongoing ? 'started' : 'finished',
        observedAt: now,
        startedAt,
        finishedAt,
        players,
        capacity: null,
        map: map ?? null,
        mode,
        visibility: 'public',
        confidence: 'provisional',
        ...(finishedAt !== null
          ? { elapsedDuration: { value: (finishedAt - startedAt) / 1000, unit: 'seconds' } }
          : {}),
      },
    };
  } catch {
    return { status: 'invalid_response' };
  }
}

export function createAoE4WorldReader(runtime: AdapterRuntime = {}) {
  const request = runtime.request ?? providerJson;
  async function discoverOngoing(): Promise<ProviderResult<LobbyObservation[]>> {
    const now = Date.now(),
      since = Math.floor((now - lookbackMs) / 1000),
      url = new URL('/api/v0/games', origin);
    url.searchParams.set('order', 'started_at');
    url.searchParams.set('since', String(since));
    url.searchParams.set('leaderboard', 'rm_1v1');
    // The server can ignore per_page. Bound bytes and rows, and never chase next_page.
    const response = await request({ url: url.toString(), cacheTtlMs: 30000, maxBytes: 1000000 });
    if (response.status !== 'ok') return response;
    try {
      const rows = array(applicationObject(response.value).games, 200),
        ids = new Set<string>(),
        observations: LobbyObservation[] = [];
      for (const value of rows) {
        const game = record(value),
          id = ageIdentifier(game.game_id);
        if (ids.has(id) || typeof game.ongoing !== 'boolean') throw Error('Invalid game list');
        ids.add(id);
        if (!game.ongoing) continue;
        const decoded = decodeAoE4WorldGame(game, now);
        if (decoded.status !== 'ok') return decoded;
        if (decoded.value.mode !== 'rm_1v1' || decoded.value.startedAt! < since * 1000) continue;
        observations.push(decoded.value);
      }
      return {
        status: 'ok',
        value: observations.sort(
          (a, b) => b.startedAt! - a.startedAt! || a.externalId.localeCompare(b.externalId),
        ),
      };
    } catch {
      return { status: 'invalid_response' };
    }
  }
  async function observe(
    reference: string,
    context: ObserveContext = {},
  ): Promise<ProviderResult<LobbyObservation>> {
    const id = worldReferenceId(reference);
    if (!id) return { status: 'not_found' };
    const previous = context.previous;
    let expected: string[];
    try {
      expected = (
        context.profileIds?.length
          ? context.profileIds
          : (previous?.players.map((p) => p.profileId) ?? [])
      )
        .map(ageIdentifier)
        .sort();
      if (expected.length < 2 || expected.length > 16) return delayed();
      if (new Set(expected).size !== expected.length) throw Error('Duplicate expected profile');
      if (
        previous &&
        (previous.externalId !== reference ||
          previous.referenceKind !== 'match' ||
          previous.provider !== 'aoe4world' ||
          previous.providerIds?.aoe4world !== id ||
          profiles(previous.players) !== JSON.stringify(expected))
      )
        throw Error('Conflicting provider binding');
    } catch {
      return { status: 'invalid_response' };
    }
    const anchor = context.matchCreatedAt ?? context.attachedAt;
    if (
      (anchor !== undefined &&
        (!Number.isSafeInteger(anchor) || anchor <= 0 || anchor > Date.now())) ||
      (!previous && !anchor)
    )
      return delayed();
    const response = await request({
      url: `${origin}/api/v0/players/${expected[0]}/games/${id}`,
      cacheTtlMs: 30000,
      maxBytes: 2000000,
    });
    if (response.status !== 'ok') return response.status === 'not_found' ? delayed() : response;
    const decoded = decodeAoE4WorldGame(response.value);
    if (decoded.status !== 'ok') return decoded;
    const observation = decoded.value;
    if (
      observation.externalId !== reference ||
      profiles(observation.players) !== JSON.stringify(expected) ||
      (anchor !== undefined && observation.startedAt! < anchor - 120000) ||
      (previous &&
        (!sameAoE4Match(previous, observation) ||
          (previous.phase === 'finished' && observation.phase !== 'finished')))
    )
      return { status: 'invalid_response' };
    return { status: 'ok', value: { ...observation, confidence: 'roster' } };
  }
  return { discoverOngoing, observe };
}

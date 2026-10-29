import { gameCapabilities } from './catalog';
import { providerJson } from './transport';
import type {
  AdapterRuntime,
  GameAdapter,
  ProviderProfile,
  ProviderResult,
  SupportingMatchEvidence,
} from './types';

const hosts = { '1': 'us', '2': 'eu', '3': 'kr' } as const;
export interface Sc2Character {
  region: keyof typeof hosts;
  realm: string;
  profile: string;
  id: string;
}
const numeric = (value: unknown): string | null =>
  typeof value === 'string' && /^[1-9]\d{0,19}$/.test(value)
    ? value
    : typeof value === 'number' && Number.isSafeInteger(value) && value > 0
      ? String(value)
      : null;
const record = (value: unknown): Record<string, unknown> => {
  if (!value || typeof value !== 'object' || Array.isArray(value))
    throw new Error('Invalid SC2 response');
  return value as Record<string, unknown>;
};
export function sc2Character(input: string): Sc2Character {
  let value = input.trim();
  const toon = /^(1|2|3)-S2-([12])-([1-9]\d{0,19})$/.exec(value);
  if (toon) value = `${toon[1]}:${toon[2]}:${toon[3]}`;
  if (value.startsWith('https://')) {
    const url = new URL(value);
    const path = /^\/[a-z]{2}-[a-z]{2}\/profile\/([123])\/([12])\/([1-9]\d{0,19})\/?$/.exec(
      url.pathname,
    );
    if (
      !['starcraft2.blizzard.com', 'starcraft2.com'].includes(url.hostname) ||
      url.port ||
      url.username ||
      url.password ||
      url.search ||
      url.hash ||
      !path
    )
      throw new Error('Use an SC2 character ID in region:realm:profile format.');
    value = `${path[1]}:${path[2]}:${path[3]}`;
  }
  const match = /^([123]):([12]):([1-9]\d{0,19})$/.exec(value);
  if (!match)
    throw new Error(
      'Use an SC2 character ID in region:realm:profile format (regions 1=US, 2=EU, 3=KR/TW).',
    );
  return { region: match[1] as keyof typeof hosts, realm: match[2], profile: match[3], id: value };
}
export function sc2ProfileUrl(id: string): string {
  const c = sc2Character(id);
  return `https://starcraft2.blizzard.com/en-us/profile/${c.region}/${c.realm}/${c.profile}`;
}
function applicationError(value: unknown): ProviderResult<never> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const data = value as Record<string, unknown>;
  if (data.code === 401 || data.code === 403 || data.error === 'invalid_token')
    return { status: 'authentication_required' };
  if (data.code === 404) return { status: 'not_found' };
  if (data.code === 429) return { status: 'throttled' };
  if (data.code || data.error) return { status: 'invalid_response' };
  return null;
}
async function get(runtime: AdapterRuntime, region: keyof typeof hosts, path: string) {
  if (!runtime.battleNetAccessToken) return { status: 'authentication_required' } as const;
  return (runtime.request ?? providerJson)({
    url: `https://${hosts[region]}.api.blizzard.com${path}?locale=en_US`,
    headers: { Authorization: `Bearer ${runtime.battleNetAccessToken}` },
    cacheTtlMs: 60000,
    maxBytes: 262144,
  });
}
export function decodeSc2Profile(value: unknown, id: string): ProviderProfile {
  const c = sc2Character(id),
    root = record(value),
    summary = record(root.summary);
  if (
    numeric(summary.id) !== c.profile ||
    String(summary.realm) !== c.realm ||
    typeof summary.displayName !== 'string' ||
    !summary.displayName.trim()
  )
    throw new Error('SC2 profile identity mismatch');
  return {
    id: c.id,
    name: summary.displayName.slice(0, 100),
    url: sc2ProfileUrl(c.id),
    namespace: 'sc2-character',
    edition: 'StarCraft II',
  };
}
export function decodeSc2Characters(value: unknown): ProviderProfile[] {
  if (!Array.isArray(value) || value.length > 100) throw new Error('Invalid SC2 characters');
  const profiles = value.map((raw) => {
    const row = record(raw),
      profile = numeric(row.profileId),
      region = numeric(row.regionId),
      realm = numeric(row.realmId);
    if (!profile || !region || !realm || typeof row.name !== 'string' || !row.name.trim())
      throw new Error('Invalid SC2 character');
    const id = sc2Character(`${region}:${realm}:${profile}`).id;
    return {
      id,
      name: row.name.slice(0, 100),
      url: sc2ProfileUrl(id),
      namespace: 'sc2-character',
      edition: 'StarCraft II',
    };
  });
  if (new Set(profiles.map((p) => p.id)).size !== profiles.length)
    throw new Error('Duplicate SC2 character');
  return profiles;
}
export interface Sc2OwnedCharacters {
  profiles: ProviderProfile[];
  unavailableRegions: string[];
}
/** OAuth's account ID is supplied from authenticated userinfo, never from a request body. */
export async function fetchOwnedSc2Characters(
  accountId: string,
  runtime: AdapterRuntime,
): Promise<ProviderResult<Sc2OwnedCharacters>> {
  if (!numeric(accountId)) return { status: 'invalid_response' };
  const profiles = new Map<string, ProviderProfile>(),
    unavailableRegions: string[] = [];
  let successful = 0;
  for (const region of ['1', '2', '3'] as const) {
    const response = await get(runtime, region, `/sc2/player/${accountId}`);
    if (response.status !== 'ok') {
      if (response.status === 'authentication_required') return response;
      unavailableRegions.push(hosts[region]);
      continue;
    }
    const error = applicationError(response.value);
    if (error) {
      if (error.status === 'authentication_required') return error;
      unavailableRegions.push(hosts[region]);
      continue;
    }
    try {
      for (const profile of decodeSc2Characters(response.value)) profiles.set(profile.id, profile);
      successful++;
    } catch {
      unavailableRegions.push(hosts[region]);
    }
  }
  if (!successful) return { status: 'unavailable' };
  return {
    status: 'ok',
    value: {
      profiles: [...profiles.values()].sort((a, b) => a.id.localeCompare(b.id)),
      unavailableRegions,
    },
  };
}
export type Sc2SupportingMatchEvidence = SupportingMatchEvidence;
/** Legacy history has no full roster, native match ID, duration, or lifecycle proof. */
export function decodeSc2History(
  value: unknown,
  id: string,
  now = Date.now(),
): Sc2SupportingMatchEvidence[] {
  sc2Character(id);
  const root = record(value);
  if (!Array.isArray(root.matches) || root.matches.length > 100)
    throw new Error('Invalid SC2 history');
  const outcomes: Record<string, Sc2SupportingMatchEvidence['outcome']> = {
    WIN: 'win',
    LOSS: 'loss',
    TIE: 'draw',
    DRAW: 'draw',
  };
  return root.matches
    .map((raw) => {
      const row = record(raw);
      const seconds =
        typeof row.date === 'number'
          ? row.date
          : typeof row.date === 'string' && /^\d+$/.test(row.date)
            ? Number(row.date)
            : NaN;
      if (!Number.isSafeInteger(seconds) || seconds < 946684800 || seconds * 1000 > now + 30000)
        throw new Error('Invalid SC2 history date');
      if (row.map !== undefined && typeof row.map !== 'string')
        throw new Error('Invalid SC2 history map');
      return {
        source: 'battle-net-legacy-history',
        observedAt: now,
        accountId: id,
        occurredAt: seconds * 1000,
        ...(row.map ? { map: String(row.map).slice(0, 100) } : {}),
        ...(typeof row.type === 'string' ? { mode: row.type.slice(0, 100) } : {}),
        outcome: outcomes[String(row.decision).toUpperCase()] ?? 'unknown',
      };
    })
    .sort((a, b) => b.occurredAt! - a.occurredAt!);
}
export async function fetchSc2History(
  id: string,
  runtime: AdapterRuntime = {},
): Promise<ProviderResult<Sc2SupportingMatchEvidence[]>> {
  let c: Sc2Character;
  try {
    c = sc2Character(id);
  } catch {
    return { status: 'not_found' };
  }
  const response = await get(
    runtime,
    c.region,
    `/sc2/legacy/profile/${c.region}/${c.realm}/${c.profile}/matches`,
  );
  if (response.status !== 'ok') return response;
  const error = applicationError(response.value);
  if (error) return error;
  try {
    return { status: 'ok', value: decodeSc2History(response.value, c.id) };
  } catch {
    return { status: 'invalid_response' };
  }
}
export function createSc2Adapter(
  runtime: AdapterRuntime = {},
): GameAdapter & { supportingHistory: typeof fetchSc2History } {
  return {
    id: 'sc2',
    manifest: gameCapabilities('sc2')!.manifest,
    profileProvider: 'battle-net',
    parseAccountReference: (input) => sc2Character(input).id,
    parseLobbyReference(input) {
      const value = input.trim();
      if (!value || value.length > 200 || /[\u0000-\u001f\u007f]/.test(value))
        throw new Error('Enter the host-supplied lobby name or invitation.');
      return value;
    },
    async profile(input) {
      let c: Sc2Character;
      try {
        c = sc2Character(input);
      } catch {
        return { status: 'not_found' };
      }
      const response = await get(
        runtime,
        c.region,
        `/sc2/profile/${c.region}/${c.realm}/${c.profile}`,
      );
      if (response.status !== 'ok') return response;
      const error = applicationError(response.value);
      if (error) return error;
      try {
        return { status: 'ok', value: decodeSc2Profile(response.value, c.id) };
      } catch {
        return { status: 'invalid_response' };
      }
    },
    observe: async () => ({ status: 'unsupported' }),
    supportingHistory: (id) => fetchSc2History(id, runtime),
    profileUrl: sc2ProfileUrl,
    lobbyUrl: () => '',
    joinUri: () => '',
  };
}

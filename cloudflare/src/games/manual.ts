import { isSteamIndividualId } from './steam';
import { gameCapabilities } from './catalog';
import type { CatalogId, GameAdapter, ReplayReference } from './types';

type ManualGame = Extract<CatalogId, 'generals' | 'northgard' | 'tempest_rising'>;
const unsafeText = /[\u0000-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069]/;
const steamApps = { northgard: '466560', tempest_rising: '1486920' } as const;
/** Declared Steam identities remain strings; validation is not proof of ownership. */
export function parseSteamAccountReference(input: string): string {
  let id = input.trim();
  if (unsafeText.test(input)) throw Error('Enter a SteamID64 or a numeric Steam profile link.');
  if (!/^\d{17}$/.test(id)) {
    try {
      const u = new URL(id);
      if (
        u.protocol !== 'https:' ||
        u.hostname !== 'steamcommunity.com' ||
        u.username ||
        u.password ||
        u.port ||
        u.search ||
        u.hash
      )
        throw Error('Invalid Steam link');
      id = /^\/profiles\/(\d{17})\/?$/.exec(u.pathname)?.[1] ?? '';
    } catch {
      id = '';
    }
  }
  // Public individual accounts, desktop instance, uint32 account ID; never convert to Number.
  if (!isSteamIndividualId(id)) throw Error('Enter a SteamID64 or a numeric Steam profile link.');
  return id;
}

export function parseGeneralsAccountReference(input: string): string {
  const value = input.trim();
  const matched = /^([a-z][a-z0-9_-]{0,23}):([A-Za-z0-9][A-Za-z0-9_.@-]{0,63})$/i.exec(value);
  if (!matched || unsafeText.test(input))
    throw Error('Declare the network and account ID as network:id, for example gameranger:12345.');
  return `${matched[1].toLowerCase()}:${matched[2]}`;
}

/** Manual lobby references are user declarations, never URLs the worker fetches. */
export function parseManualLobbyReference(input: string, game: ManualGame): string {
  const value = input.trim();
  if (!value || value.length > 100 || unsafeText.test(input))
    throw Error('Enter a lobby ID, room name or invite of at most 100 characters.');
  if (
    /^[a-z][a-z\d+.-]*:\/\//i.test(value) ||
    /^(?:https?|steam|javascript|data|file|vbscript|ftp):/i.test(value) ||
    value.startsWith('//')
  ) {
    let u: URL;
    try {
      u = new URL(value);
    } catch {
      throw Error('Enter a lobby ID or a supported invite link.');
    }
    if (u.username || u.password || u.port)
      throw Error('Lobby links cannot contain credentials or a custom port.');
    if (u.protocol === 'https:' && u.hostname && !/\s/.test(value) && u.toString().length <= 100)
      return u.toString();
    if (
      game !== 'generals' &&
      u.protocol === 'steam:' &&
      u.hostname === 'joinlobby' &&
      new RegExp(`^/${steamApps[game]}/[0-9]{1,20}/[0-9]{1,20}/?$`).test(u.pathname) &&
      !u.search &&
      !u.hash
    )
      return u.toString();
    throw Error('Use an HTTPS invite, a matching Steam lobby invite or a plain lobby ID.');
  }
  return value;
}

/** References only: no download, upload, storage retention or availability claim. */
export function externalRecordingReference(url: string, matchId: string): ReplayReference {
  if (
    url.length > 2000 ||
    unsafeText.test(url) ||
    !matchId ||
    matchId.length > 100 ||
    unsafeText.test(matchId)
  )
    throw Error('Invalid external recording reference.');
  let u: URL;
  try {
    u = new URL(url);
  } catch {
    throw Error('Enter an HTTPS recording reference.');
  }
  if (
    u.protocol !== 'https:' ||
    !u.hostname ||
    u.username ||
    u.password ||
    u.port ||
    u.toString().length > 2000
  )
    throw Error('Enter an HTTPS recording reference without credentials or a custom port.');
  return {
    provider: 'declared-external',
    matchId,
    availability: 'unknown',
    sourceUrl: u.toString(),
  };
}

export function createManualAdapter(id: ManualGame): GameAdapter {
  const catalog = gameCapabilities(id)!;
  const parseAccount =
    id === 'generals' ? parseGeneralsAccountReference : parseSteamAccountReference;
  return {
    id,
    manifest: catalog.manifest,
    profileProvider: catalog.profileProvider,
    parseLobbyReference: (input) => parseManualLobbyReference(input, id),
    parseAccountReference: parseAccount,
    // Account declarations and authenticated ownership callbacks are handled by the shared workflow.
    async profile() {
      return { status: 'unsupported' };
    },
    async observe() {
      return { status: 'unsupported' };
    },
    profileUrl(account) {
      const normalized = parseAccount(account);
      return id === 'generals' ? '' : `https://steamcommunity.com/profiles/${normalized}`;
    },
    joinUri(reference) {
      const normalized = parseManualLobbyReference(reference, id);
      return /^(https|steam):/.test(normalized) ? normalized : '';
    },
    lobbyUrl(reference) {
      const normalized = parseManualLobbyReference(reference, id);
      return normalized.startsWith('https:') ? normalized : '';
    },
  };
}

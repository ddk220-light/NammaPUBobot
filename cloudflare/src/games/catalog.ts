import type { CatalogId, ConnectorManifest, ConnectorOperation, Capability } from './types';

/** A catalog version describes metadata, not a provider schema or game build. */
export const connectorCatalogVersion = '2026-10-28.1';
const allOperations: Record<ConnectorOperation, Capability> = {
  accountLookup: 'available',
  ownershipVerification: 'available',
  lobbyDiscovery: 'available',
  lobbyObservation: 'available',
  matchLookup: 'available',
  historyLookup: 'available',
  replayReferences: 'available',
};
const noOperations: Record<ConnectorOperation, Capability> = {
  accountLookup: 'unsupported',
  ownershipVerification: 'unsupported',
  lobbyDiscovery: 'unsupported',
  lobbyObservation: 'unsupported',
  matchLookup: 'unsupported',
  historyLookup: 'unsupported',
  replayReferences: 'unsupported',
};
function manifest(
  id: CatalogId,
  edition: string,
  provider: string,
  accountNamespace: string,
  capabilities: Record<ConnectorOperation, Capability>,
  limitations: string[],
): ConnectorManifest {
  return {
    id,
    edition,
    provider,
    accountNamespace,
    version: connectorCatalogVersion,
    capabilities,
    acceptance: 'pending',
    limitations,
  };
}

export interface GameCatalogEntry {
  id: CatalogId;
  name: string;
  selection: 'civilization' | 'god' | 'faction' | 'race' | 'clan';
  /** Provider observation support. Availability and release gates remain separate. */
  lobby: boolean;
  occupancy: boolean;
  profileProvider: string;
  manual: true;
  manifest: ConnectorManifest;
}
const ageLimitations = [
  'Steam ownership requires a verified mapping to the native profile; public profile existence is not ownership proof.',
  'Public lobbies and histories are incomplete. Missing or private history does not establish a result.',
  'Lobby-to-match correlation requires a matching roster and time window; ambiguous rematches remain manual.',
  'Automatic observations require controlled game acceptance. Captains confirm every result.',
];
/** The single capability source for adapters, setup UI, health diagnostics and contract tests. */
export const gameCatalog: readonly GameCatalogEntry[] = [
  {
    id: 'aoe2',
    name: 'Age of Empires II',
    selection: 'civilization',
    lobby: true,
    occupancy: true,
    profileProvider: 'aoe2',
    manual: true,
    manifest: manifest(
      'aoe2',
      'Age of Empires II: Definitive Edition',
      'aoe2companion',
      'aoe2:profile',
      { ...allOperations },
      [
        'A shared Companion feed supplies public lobby occupancy. Lobby disappearance is not start evidence.',
        'Replay links are conditional provider references, not archived recordings.',
        'Public profile existence is not ownership proof. Captains confirm every result.',
      ],
    ),
  },
  {
    id: 'aoe3',
    name: 'Age of Empires III',
    selection: 'civilization',
    lobby: true,
    occupancy: true,
    profileProvider: 'aoe3',
    manual: true,
    manifest: manifest(
      'aoe3',
      'Age of Empires III: Definitive Edition',
      'worlds-edge',
      'worlds-edge:age3',
      { ...allOperations },
      [
        ...ageLimitations,
        'Replay metadata lookup is supported; recordings were unavailable in the research sample.',
        'Unknown civilization IDs are preserved rather than substituted.',
      ],
    ),
  },
  {
    id: 'aoe4',
    name: 'Age of Empires IV',
    selection: 'civilization',
    lobby: true,
    occupancy: true,
    profileProvider: 'aoe4',
    manual: true,
    manifest: manifest(
      'aoe4',
      'Age of Empires IV',
      'worlds-edge',
      'worlds-edge:age4',
      { ...allOperations },
      [
        ...ageLimitations,
        'AoE4 World enrichment is optional; its 404 is not proof that a native match does not exist.',
        'Custom modes may not provide a supported two-team outcome.',
      ],
    ),
  },
  {
    id: 'aom',
    name: 'Age of Mythology',
    selection: 'god',
    lobby: true,
    occupancy: true,
    profileProvider: 'aom',
    manual: true,
    manifest: manifest(
      'aom',
      'Age of Mythology: Retold',
      'worlds-edge',
      'worlds-edge:athens',
      { ...allOperations },
      [
        ...ageLimitations,
        'This adapter covers Retold only; earlier Mythology editions use manual reporting.',
        'Replay URLs may expire. Request fresh metadata before following a temporary reference.',
      ],
    ),
  },
  {
    id: 'generals',
    name: 'Command & Conquer Generals',
    selection: 'faction',
    lobby: false,
    occupancy: false,
    profileProvider: 'generals-network',
    manual: true,
    manifest: manifest(
      'generals',
      'Command & Conquer Generals (original)',
      'manual',
      'generals-network',
      { ...noOperations },
      [
        'Declare accounts as network:id. A declaration does not prove account ownership.',
        'Hosts supply lobby references and participants supply match facts. Captains confirm results.',
        'External recording references are accepted without fetching their contents.',
        'GeneralsOnline and Zero Hour compatibility are not assumed for original Generals.',
      ],
    ),
  },
  {
    id: 'zero_hour',
    name: 'Command & Conquer Generals: Zero Hour',
    selection: 'faction',
    lobby: true,
    occupancy: true,
    profileProvider: 'generalsonline',
    manual: true,
    manifest: manifest(
      'zero_hour',
      'Command & Conquer Generals: Zero Hour',
      'generalsonline',
      'generalsonline:user',
      {
        ...noOperations,
        lobbyDiscovery: 'credentials_required',
        lobbyObservation: 'credentials_required',
        matchLookup: 'credentials_required',
        replayReferences: 'credentials_required',
      },
      [
        'GeneralsOnline requires a supported integration key and verified production permissions.',
        'Native account declarations do not prove ownership; no public third-party account crosswalk is assumed.',
        'Player history is unsupported. A lobby can supply a distinct native match ID.',
        'Missing credentials leave manual reporting available. Automatic observations require controlled game acceptance.',
      ],
    ),
  },
  {
    id: 'sc2',
    name: 'StarCraft II',
    selection: 'race',
    lobby: false,
    occupancy: false,
    profileProvider: 'battle-net',
    manual: true,
    manifest: manifest(
      'sc2',
      'StarCraft II',
      'battle-net',
      'sc2-character',
      {
        ...noOperations,
        accountLookup: 'credentials_required',
        ownershipVerification: 'credentials_required',
        historyLookup: 'credentials_required',
      },
      [
        'Characters are identified by region:realm:profile, not BattleTag or display name.',
        'Official history is limited supporting evidence; it does not establish complete rosters or native lobby continuity.',
        'Lobby, faction, duration and result remain manual when the API cannot establish them.',
      ],
    ),
  },
  {
    id: 'bar',
    name: 'Beyond All Reason',
    selection: 'faction',
    lobby: true,
    occupancy: true,
    profileProvider: 'bar',
    manual: true,
    manifest: manifest(
      'bar',
      'Beyond All Reason',
      'bar',
      'bar:user',
      { ...allOperations, ownershipVerification: 'credentials_required' },
      [
        'Verified account linking requires a registered BAR OAuth client.',
        'Public battle roster lookup may not find offline users; absence does not invalidate a declaration.',
        'Battle ID, server match ID and replay hash are distinct. Same-room rematches require unambiguous correlation.',
        'Live ally teams may be unknown. Abnormal endings and uncertain outcomes remain manual.',
        'Automatic observations require controlled game acceptance. Captains confirm every result.',
      ],
    ),
  },
  {
    id: 'northgard',
    name: 'Northgard',
    selection: 'clan',
    lobby: false,
    occupancy: false,
    profileProvider: 'steam',
    manual: true,
    manifest: manifest(
      'northgard',
      'Northgard (Steam PC)',
      'manual',
      'steam',
      { ...noOperations, ownershipVerification: 'available' },
      [
        'Steam OpenID proves Steam account control, not game ownership or match participation.',
        'Hosts supply lobby IDs; clans, teams, duration and results are declared and captain-confirmed.',
        'No public match feed is established. Client injection and local companions are outside this connector.',
      ],
    ),
  },
  {
    id: 'tempest_rising',
    name: 'Tempest Rising',
    selection: 'faction',
    lobby: false,
    occupancy: false,
    profileProvider: 'steam',
    manual: true,
    manifest: manifest(
      'tempest_rising',
      'Tempest Rising (Steam PC)',
      'manual',
      'steam',
      { ...noOperations, ownershipVerification: 'available' },
      [
        'Steam OpenID proves Steam account control, not game ownership or match participation.',
        'Hosts supply a lobby reference or invite; factions, teams, duration and results are declared and captain-confirmed.',
        'No public match feed is established. Experimental map-editor exporters are outside this connector.',
      ],
    ),
  },
];
export const gameCapabilities = (id: string | null | undefined) =>
  gameCatalog.find((game) => game.id === id);

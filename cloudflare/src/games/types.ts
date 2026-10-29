/** Providers supply evidence; only the existing captain workflow can settle a match. */
export const catalogIds = [
  'aoe2',
  'aoe3',
  'aoe4',
  'aom',
  'generals',
  'zero_hour',
  'sc2',
  'bar',
  'northgard',
  'tempest_rising',
  'wc3',
] as const;
export type CatalogId = (typeof catalogIds)[number];
export const operations = [
  'accountLookup',
  'ownershipVerification',
  'lobbyDiscovery',
  'ongoingDiscovery',
  'lobbyObservation',
  'matchLookup',
  'historyLookup',
  'replayReferences',
] as const;
export type ConnectorOperation = (typeof operations)[number];
export type Capability = 'available' | 'credentials_required' | 'unsupported';
export interface ConnectorManifest {
  id: CatalogId;
  edition: string;
  provider: string;
  accountNamespace: string;
  version: string;
  capabilities: Record<ConnectorOperation, Capability>;
  /** Newly shipped automation stays disabled until controlled game acceptance is recorded. */
  acceptance: 'pending' | 'legacy';
  limitations: string[];
  supportedModes?: string[];
}
export type ProviderFailure =
  | 'not_found'
  | 'delayed'
  | 'unavailable'
  | 'throttled'
  | 'authentication_required'
  | 'unsupported'
  | 'invalid_response';
export type ProviderResult<T> =
  { status: 'ok'; value: T } | { status: ProviderFailure; retryAfterMs?: number };
export interface ReplayReference {
  provider: string;
  matchId: string;
  perspectiveId?: string;
  availability: 'available' | 'unavailable' | 'unknown' | 'expired';
  /** Provider landing page or temporary provider URL. Never an archived object. */
  sourceUrl?: string;
  expiresAt?: number;
}
export interface LobbyPlayer {
  profileId: string;
  name: string;
  team: string | null;
  selection?: string;
  selectionId?: string;
  outcome?: 'win' | 'loss' | 'draw' | 'unknown';
  accountNamespace?: string;
}
export interface LobbyObservation {
  /** Compatibility alias for the bound native lobby ID, not a bot match ID. */
  externalId: string;
  referenceKind?: 'lobby' | 'match';
  providerIds?: Record<string, string>;
  name: string;
  phase: 'open' | 'full' | 'started' | 'finished';
  observedAt: number;
  startedAt: number | null;
  finishedAt: number | null;
  players: LobbyPlayer[];
  capacity: number | null;
  map: string | null;
  source: string;
  schemaVersion?: 2;
  edition?: string;
  provider?: string;
  accountNamespace?: string;
  nativeLobbyId?: string;
  nativeMatchId?: string;
  platformLobbyId?: string;
  gameBuild?: string;
  elapsedDuration?: { value: number; unit: 'seconds' };
  simulationDuration?: { value: number; unit: 'seconds' };
  confidence?: 'provisional' | 'roster' | 'confirmed';
  bindingGeneration?: string;
  replayReferences?: ReplayReference[];
  /** Private/unlisted/custom modes must never be inferred from missing history. */
  visibility?: 'public' | 'private' | 'unknown';
  mode?: string;
}
export interface ProviderProfile {
  id: string;
  name: string;
  url: string;
  namespace?: string;
  edition?: string;
  platformId?: string;
}
export interface JsonRequest {
  operation?: ConnectorOperation;
  url: string;
  headers?: Record<string, string>;
  maxBytes?: number;
  cacheTtlMs?: number;
}
export type JsonTransport = (request: JsonRequest) => Promise<ProviderResult<unknown>>;
export interface AdapterRuntime {
  aoe2Discovery?: () => Promise<ProviderResult<LobbyObservation[]>>;
  aoe2OngoingDiscovery?: () => Promise<ProviderResult<LobbyObservation[]>>;
  request?: JsonTransport;
  generalsOnlineApiKey?: string;
  battleNetAccessToken?: string;
}
export interface ObserveContext {
  matchCreatedAt?: number;
  attachedAt?: number;
  profileIds?: string[];
  nativeMatchId?: string;
  previous?: LobbyObservation;
}
export interface SupportingMatchEvidence {
  source: string;
  observedAt: number;
  accountId: string;
  occurredAt: number | null;
  map?: string;
  outcome?: 'win' | 'loss' | 'draw' | 'unknown';
  mode?: string;
}
export interface GameAdapter {
  id: CatalogId;
  manifest: ConnectorManifest;
  /** Retained for existing profile_refs keys. */
  profileProvider: string;
  parseLobbyReference(input: string): string;
  parseAccountReference?(input: string): string;
  profile(id: string): Promise<ProviderResult<ProviderProfile>>;
  observe(
    id: string,
    started: boolean,
    context?: ObserveContext,
  ): Promise<ProviderResult<LobbyObservation>>;
  discover?(): Promise<ProviderResult<LobbyObservation[]>>;
  discoverOngoing?(): Promise<ProviderResult<LobbyObservation[]>>;
  match?(id: string): Promise<ProviderResult<LobbyObservation>>;
  history?(profileId: string): Promise<ProviderResult<LobbyObservation[]>>;
  replays?(matchId: string): Promise<ProviderResult<ReplayReference[]>>;
  supportingHistory?(profileId: string): Promise<ProviderResult<SupportingMatchEvidence[]>>;
  resolveSteam?(steamId: string): Promise<ProviderResult<ProviderProfile>>;
  profileUrl(id: string): string;
  joinUri(id: string): string;
  /** HTTPS landing page, or empty when the provider has no appropriate public page. */
  lobbyUrl(id: string): string;
}

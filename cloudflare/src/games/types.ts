/** Game providers supply evidence; they never write ratings, wallets or Discord messages. */
export type CatalogId = 'aoe2' | 'aoe3' | 'aoe4' | 'aom' | 'generals';
export interface LobbyPlayer {
  profileId: string;
  name: string;
  team: string | null;
}
export interface LobbyObservation {
  externalId: string;
  name: string;
  phase: 'open' | 'full' | 'started' | 'finished';
  observedAt: number;
  startedAt: number | null;
  finishedAt: number | null;
  players: LobbyPlayer[];
  capacity: number | null;
  map: string | null;
  source: string;
}
export type ProviderResult<T> =
  { status: 'ok'; value: T } | { status: 'not_found' | 'unavailable'; retryAfterMs?: number };
export interface GameAdapter {
  id: CatalogId;
  profileProvider: string;
  parseLobbyReference(input: string): string;
  profile(id: string): Promise<ProviderResult<{ id: string; name: string; url: string }>>;
  observe(id: string, started: boolean): Promise<ProviderResult<LobbyObservation>>;
  joinUri(id: string): string;
  /** HTTPS landing page suitable for Discord link buttons. */
  lobbyUrl(id: string): string;
}

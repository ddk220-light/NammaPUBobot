import type { ProviderCoordinator } from './storage/provider-coordinator';
import type { AoE2LobbyFeed } from './storage/aoe2-lobby-feed';
import type { Community } from './storage/community';
import type { AuthStore } from './storage/auth';
import type { DiscordGate } from './storage/discord-gate';
import type { VoicePresence } from './storage/voice-presence';
export interface Env {
  ASSETS: Fetcher;
  COMMUNITIES: DurableObjectNamespace<Community>;
  AUTH: DurableObjectNamespace<AuthStore>;
  DISCORD_GATE: DurableObjectNamespace<DiscordGate>;
  AOE2_LOBBIES: DurableObjectNamespace<AoE2LobbyFeed>;
  PROVIDERS?: DurableObjectNamespace<ProviderCoordinator>;
  CONNECTOR_ACCEPTED_GAMES?: string;
  CONNECTOR_ACCEPTED_CAPABILITIES?: string;
  CONNECTOR_DISABLED?: string;
  GENERALS_ONLINE_API_KEY?: string;
  BATTLENET_CLIENT_ID?: string;
  BATTLENET_CLIENT_SECRET?: string;
  BAR_CLIENT_ID?: string;
  BAR_CLIENT_SECRET?: string;
  VOICE_PRESENCE: DurableObjectNamespace<VoicePresence>;
  APP_ENV: 'local' | 'staging' | 'production';
  APP_ORIGIN: string;
  DISCORD_APPLICATION_ID?: string;
  DISCORD_PUBLIC_KEY?: string;
  DISCORD_CLIENT_SECRET?: string;
  DISCORD_BOT_TOKEN?: string;
  OPS_TOKEN?: string;
  CONNECTOR_TOKEN?: string;
}
export interface Session {
  user: { id: string; username: string; avatar: string | null };
  accessToken: string;
  expiresAt: number;
  csrf: string;
}

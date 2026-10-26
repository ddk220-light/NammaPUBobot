import type { Community } from './storage/community';
import type { AuthStore } from './storage/auth';
import type { DiscordGate } from './storage/discord-gate';
import type { VoicePresence } from './storage/voice-presence';
export interface Env {
  ASSETS: Fetcher;
  COMMUNITIES: DurableObjectNamespace<Community>;
  AUTH: DurableObjectNamespace<AuthStore>;
  DISCORD_GATE: DurableObjectNamespace<DiscordGate>;
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

import type { Queue } from './config';

export interface GameState {
  id: string;
  ratingEngineLocked: boolean;
  ratingEngine: Queue['ratingEngine'] | null;
}

/** Shipped capabilities, independent of a community's enabled modules. */
export const capabilities = {
  matchmaking: true,
  gold: true,
  predictions: true,
  quiz: false,
  civs: true,
  maps: true,
  stories: true,
} as const;

export interface SetupRole {
  id: string;
  name: string;
  rankEligible: boolean;
  reason: string | null;
}
export interface DiscordSetup {
  channels: { id: string; name: string; permissions: { name: string; granted: boolean }[] }[];
  moderatorRoles: { id: string; name: string }[];
  roles: SetupRole[];
  voiceChannels: { id: string; name: string }[];
  access: {
    status: 'ready' | 'needs_access' | 'unavailable';
    message: string;
    manageRoles: boolean;
    manageNicknames: boolean;
    retryAfter?: string;
  };
}

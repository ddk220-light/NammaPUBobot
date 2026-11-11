import { DurableObject } from 'cloudflare:workers';
import type { Env, Session } from '../env';
import type { Guild } from '../discord/client';
import type { GameVerificationState, GameAccountChoiceState } from '../auth/game-accounts';

export class AuthStore extends DurableObject<Env> {
  async createGameVerification(state: GameVerificationState) {
    await this.ctx.storage.transaction(async (tx) => {
      await tx.put('game-verification', state);
      await tx.setAlarm(state.expiresAt);
    });
  }
  async consumeGameVerification(
    sessionId: string,
    userId: string,
  ): Promise<GameVerificationState | null> {
    return this.ctx.storage.transaction(async (tx) => {
      const state = await tx.get<GameVerificationState>('game-verification');
      if (!state || state.expiresAt <= Date.now()) {
        await tx.delete('game-verification');
        return null;
      }
      // A different Discord account/session cannot consume another user's attempt.
      if (state.sessionId !== sessionId || state.discordUserId !== userId) return null;
      await tx.delete('game-verification');
      return state;
    });
  }
  async createGameChoices(state: GameAccountChoiceState) {
    await this.ctx.storage.transaction(async (tx) => {
      await tx.put('game-choices', state);
      await tx.setAlarm(state.expiresAt);
    });
  }
  async consumeGameChoices(
    sessionId: string,
    userId: string,
  ): Promise<GameAccountChoiceState | null> {
    return this.ctx.storage.transaction(async (tx) => {
      const state = await tx.get<GameAccountChoiceState>('game-choices');
      if (!state || state.expiresAt <= Date.now()) {
        await tx.delete('game-choices');
        return null;
      }
      if (state.sessionId !== sessionId || state.discordUserId !== userId) return null;
      await tx.delete('game-choices');
      return state;
    });
  }
  async createState(expiresAt: number, returnTo = '/') {
    await this.ctx.storage.transaction(async (tx) => {
      await tx.put('state', { expiresAt, returnTo });
      await tx.setAlarm(expiresAt);
    });
  }
  async returnPath() {
    return (await this.ctx.storage.get<{ returnTo?: string }>('state'))?.returnTo ?? '/';
  }
  async consumeState(): Promise<boolean> {
    return this.ctx.storage.transaction(async (tx) => {
      const state = await tx.get<{ expiresAt: number }>('state');
      if (!state) return false;
      await tx.delete('state');
      return state.expiresAt > Date.now();
    });
  }
  async saveSession(session: Session) {
    await this.ctx.storage.transaction(async (tx) => {
      await tx.put('session', session);
      await tx.setAlarm(session.expiresAt);
    });
  }
  async session(): Promise<Session | null> {
    const s = await this.ctx.storage.get<Session>('session');
    return s && s.expiresAt > Date.now() ? s : null;
  }
  /** A short-lived copy of the signed-in user's Discord guilds, so one page load asks Discord once. */
  async cachedGuilds(maxAge: number): Promise<Guild[] | null> {
    const cache = await this.ctx.storage.get<{ at: number; guilds: Guild[] }>('guilds');
    return cache && Date.now() - cache.at < maxAge && Date.now() >= cache.at ? cache.guilds : null;
  }
  async cacheGuilds(guilds: Guild[]) {
    await this.ctx.storage.transaction(async (tx) => {
      // Never outlive the session: a write after sign-out would leave an orphaned object.
      if (await tx.get('session')) await tx.put('guilds', { at: Date.now(), guilds });
    });
  }
  async remove() {
    await this.ctx.storage.deleteAll();
    await this.ctx.storage.deleteAlarm();
  }
  async alarm() {
    await this.remove();
  }
}

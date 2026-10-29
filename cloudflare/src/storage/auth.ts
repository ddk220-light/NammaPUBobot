import { DurableObject } from 'cloudflare:workers';
import type { Env, Session } from '../env';
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
      await tx.delete('state');
      return !!state && state.expiresAt > Date.now();
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
  async remove() {
    await this.ctx.storage.deleteAll();
    await this.ctx.storage.deleteAlarm();
  }
  async alarm() {
    await this.remove();
  }
}

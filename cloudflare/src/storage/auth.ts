import { DurableObject } from 'cloudflare:workers';
import type { Env, Session } from '../env';

export class AuthStore extends DurableObject<Env> {
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

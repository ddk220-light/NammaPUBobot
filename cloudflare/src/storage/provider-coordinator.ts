import { battleNetClientToken, gameAccountFailure } from '../auth/game-accounts';
import { DurableObject } from 'cloudflare:workers';
import type { Env } from '../env';
import type { JsonRequest, ProviderFailure, ProviderResult } from '../games/types';
import { allowedProviderUrl, providerJson } from '../games/transport';
interface Diagnostics {
  /** Successful normalized adapter operation, not merely HTTP 200. */
  lastSuccessAt?: number;
  lastTransportSuccessAt?: number;
  lastAttemptAt?: number;
  lastFailure?: string;
  lastTransportFailure?: string;
  /** Consecutive failed normalized operations. */
  failures: number;
  totalFailures: number;
  transportFailures: number;
  throttled: number;
  throttledOperations: number;
  invalidResponses: number;
  transportInvalidResponses: number;
  retryAt: number;
}
type ClientToken = { accessToken: string; expiresAt: number };
const failures = new Set<ProviderFailure>([
  'not_found',
  'delayed',
  'unavailable',
  'throttled',
  'authentication_required',
  'unsupported',
  'invalid_response',
]);
/** One coordinator per provider host; no community-specific sockets or unbounded cache. */
export class ProviderCoordinator extends DurableObject<Env> {
  private cache = new Map<string, { until: number; result: ProviderResult<unknown> }>();
  private pending = new Map<string, Promise<ProviderResult<unknown>>>();
  private nextAt = 0;
  /** Service bearer only, in memory; user OAuth tokens never enter this object. */
  private battleToken?: ClientToken;
  private tokenPending?: Promise<ProviderResult<ClientToken>>;
  private diagnostics(): Diagnostics {
    return {
      failures: 0,
      totalFailures: 0,
      transportFailures: 0,
      throttled: 0,
      throttledOperations: 0,
      invalidResponses: 0,
      transportInvalidResponses: 0,
      retryAt: 0,
      ...this.ctx.storage.kv.get<Partial<Diagnostics>>('diagnostics'),
    };
  }
  async battleNetToken(): Promise<ProviderResult<ClientToken>> {
    if (!this.env.BATTLENET_CLIENT_ID || !this.env.BATTLENET_CLIENT_SECRET)
      return { status: 'authentication_required' };
    if (this.battleToken && this.battleToken.expiresAt > Date.now() + 60000)
      return { status: 'ok', value: this.battleToken };
    if (this.tokenPending) return this.tokenPending;
    const diagnostics = this.diagnostics();
    if (diagnostics.retryAt > Date.now())
      return {
        status: failures.has(diagnostics.lastFailure as ProviderFailure)
          ? (diagnostics.lastFailure as ProviderFailure)
          : 'unavailable',
        retryAfterMs: diagnostics.retryAt - Date.now(),
      };
    const work = (async (): Promise<ProviderResult<ClientToken>> => {
      let result: ProviderResult<ClientToken>;
      try {
        this.battleToken = await battleNetClientToken(this.env);
        result = { status: 'ok', value: this.battleToken };
      } catch (error) {
        result = gameAccountFailure(error);
      }
      const current = this.diagnostics();
      current.lastAttemptAt = Date.now();
      if (result.status === 'ok') {
        current.lastTransportSuccessAt = Date.now();
        current.retryAt = 0;
      } else {
        current.transportFailures++;
        current.lastTransportFailure = result.status;
        if (result.status === 'invalid_response') current.transportInvalidResponses++;
        if (result.status === 'throttled') current.throttled++;
        // Prevent every community from hammering token grants during an auth/provider outage.
        const delay = Math.max(
          result.retryAfterMs ?? 0,
          result.status === 'authentication_required' ? 300000 : 30000,
        );
        current.retryAt = Date.now() + Math.min(delay, 86400000);
        result = { ...result, retryAfterMs: current.retryAt - Date.now() };
      }
      this.ctx.storage.kv.put('diagnostics', current);
      await this.recordOutcome(
        result.status,
        result.status === 'ok' ? undefined : result.retryAfterMs,
      );
      return result;
    })();
    this.tokenPending = work;
    try {
      return await work;
    } finally {
      if (this.tokenPending === work) this.tokenPending = undefined;
    }
  }
  async invalidateBattleNetToken(expectedToken?: string) {
    // An old concurrent 401 must not invalidate a token that another request already refreshed.
    if (!expectedToken || this.battleToken?.accessToken === expectedToken)
      this.battleToken = undefined;
  }
  async recordOutcome(status: string, retryAfterMs?: number) {
    if (status !== 'ok' && (!failures.has(status as ProviderFailure) || status === 'unsupported'))
      return;
    const current = this.diagnostics();
    if (status === 'ok') {
      current.lastSuccessAt = Date.now();
      current.failures = 0;
      delete current.lastFailure;
    } else {
      current.lastFailure = status;
      current.failures++;
      current.totalFailures++;
      if (status === 'invalid_response') current.invalidResponses++;
      if (status === 'throttled') current.throttledOperations++;
      // Delayed/private/missing evidence is local to its binding or account, not a host outage.
      if (
        ['throttled', 'unavailable'].includes(status) &&
        retryAfterMs &&
        Number.isFinite(retryAfterMs) &&
        retryAfterMs > 0
      )
        current.retryAt = Math.max(current.retryAt, Date.now() + Math.min(retryAfterMs, 86400000));
    }
    this.ctx.storage.kv.put('diagnostics', current);
  }
  async status() {
    return this.diagnostics();
  }
  async request(input: JsonRequest): Promise<ProviderResult<unknown>> {
    if (!allowedProviderUrl(input.url)) return { status: 'invalid_response' };
    // Credentialed and replay metadata responses may contain private or temporary data: never cache them.
    const privateRequest = Object.keys(input.headers ?? {}).some((k) =>
      /authorization|api-key/i.test(k),
    );
    const cacheable = !privateRequest && !/replay/i.test(new URL(input.url).pathname);
    const key = privateRequest ? '' : input.url;
    const cached = cacheable ? this.cache.get(key) : undefined;
    if (cached && cached.until > Date.now()) return structuredClone(cached.result);
    if (key && this.pending.has(key)) return structuredClone(await this.pending.get(key)!);
    const diag = this.diagnostics();
    if (diag.retryAt > Date.now())
      return { status: 'throttled', retryAfterMs: diag.retryAt - Date.now() };
    const delay = Math.max(0, this.nextAt - Date.now());
    if (delay > 2000) return { status: 'throttled', retryAfterMs: delay };
    this.nextAt = Math.max(Date.now(), this.nextAt) + 250;
    const work = (async () => {
      if (delay) await new Promise((resolve) => setTimeout(resolve, delay));
      const retryAt = this.diagnostics().retryAt;
      if (retryAt > Date.now())
        return { status: 'throttled' as const, retryAfterMs: retryAt - Date.now() };
      const result = await providerJson(input);
      const current = this.diagnostics();
      current.lastAttemptAt = Date.now();
      if (result.status === 'ok') {
        current.lastTransportSuccessAt = Date.now();
        delete current.lastTransportFailure;
      } else {
        current.lastTransportFailure = result.status;
        current.transportFailures++;
        if (result.status === 'invalid_response') current.transportInvalidResponses++;
        if (result.status === 'throttled') current.throttled++;
        if (
          result.status === 'throttled' ||
          (result.status === 'unavailable' && result.retryAfterMs)
        )
          current.retryAt = Date.now() + Math.min(result.retryAfterMs ?? 120000, 86400000);
      }
      // Transport success must not erase a preceding schema failure or claim observation success.
      this.ctx.storage.kv.put('diagnostics', current);
      if (cacheable && result.status === 'ok') {
        if (this.cache.size >= 128) this.cache.delete(this.cache.keys().next().value!);
        this.cache.set(key, {
          result,
          until: Date.now() + Math.min(120000, Math.max(0, input.cacheTtlMs ?? 15000)),
        });
      }
      return result;
    })();
    if (key) this.pending.set(key, work);
    try {
      return structuredClone(await work);
    } finally {
      if (key) this.pending.delete(key);
    }
  }
}

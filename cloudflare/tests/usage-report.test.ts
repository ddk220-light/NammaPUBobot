import { describe, expect, it } from 'vitest';
// Pure helpers: importing the script never calls Cloudflare.
import {
  buildUsageQuery,
  formatUsage,
  summarizeUsage,
  usageWindow,
  PAID_INCLUDED,
} from '../scripts/usage-report.mjs';
// Saved responses from a real staging analysis (2026-10-12 to 2026-11-11), account tag removed.
import analytics from './fixtures/usage/graphql-2026-10-12_2026-11-11.json';
import namespaces from './fixtures/usage/namespaces.json';

const now = Date.parse('2026-11-11T12:00:00Z');
const account = 'f'.repeat(32);

describe('usage report', () => {
  it('builds one bounded analytics query over whole UTC days', () => {
    const window = usageWindow(31, now);
    expect(window).toEqual({ start: '2026-10-12', end: '2026-11-11', days: 31 });
    const query = buildUsageQuery(account, window);
    expect(query.variables).toEqual({ a: account, s: '2026-10-12', e: '2026-11-11' });
    for (const dataset of [
      'workersInvocationsAdaptive',
      'durableObjectsInvocationsAdaptiveGroups',
      'durableObjectsPeriodicGroups',
      'durableObjectsSqlStorageGroups',
    ])
      expect(query.query).toContain(dataset);
    expect(() => usageWindow(0, now)).toThrow();
    expect(() => usageWindow(32, now)).toThrow();
    expect(() => buildUsageQuery('not-an-account', window)).toThrow();
  });
  it('summarizes one Worker and only its own Durable Object namespaces', () => {
    const window = usageWindow(31, now);
    const s = summarizeUsage(analytics, namespaces, { script: 'community-gaming-staging', window });
    expect(s.totals).toMatchObject({
      workerRequests: 2170,
      workerErrors: 0,
      workerSubrequests: 3760,
      doRequests: 44927,
      doErrors: 6933,
      doRowsRead: 4028848,
      doRowsWritten: 58767,
      doStorageBytes: 5455872,
    });
    expect(s.totals.workerCpuMs).toBeCloseTo(9510.87, 2);
    expect(s.totals.doDurationGbs).toBeCloseTo(160037.98, 1);
    expect(s.invocationsByType).toEqual({ alarm: 34383, jsrpc: 10544 });
    expect(s.namespaces[0]).toMatchObject({ class: 'VoicePresence' });
    expect(s.namespaces.map((n: { class: string }) => n.class)).not.toContain(undefined);
    const duration = s.allowances.find((a: { metric: string }) => a.metric === 'doDurationGbs')!;
    expect(duration.included).toBe(PAID_INCLUDED.doDurationGbs);
    expect(duration.projected30Days).toBeCloseTo((160037.98 / 31) * 30, 0);
    expect(s.warnings).toEqual([]);
    expect(formatUsage(s)).toContain('Durable Object time    160,038 GB-s');
  });
  it('warns when duration approaches the included 400,000 GB-s', () => {
    // Ten days of the same usage projects to roughly 480,000 GB-s a month.
    const s = summarizeUsage(analytics, namespaces, {
      script: 'community-gaming-staging',
      window: usageWindow(10, now),
    });
    expect(s.warnings.join(' ')).toMatch(/doDurationGbs projects to .* of the 400,000 included/);
    expect(formatUsage(s)).toContain('WARNING: doDurationGbs');
  });
  it('flags an unknown Worker name and refuses analytics errors instead of reporting zero', () => {
    const window = usageWindow(31, now);
    expect(
      summarizeUsage(analytics, namespaces, { script: 'no-such-worker', window }).warnings,
    ).toEqual(['No traffic was recorded for Worker "no-such-worker". Check the Worker name.']);
    expect(() =>
      summarizeUsage({ errors: [{ message: 'not authorized' }] }, namespaces, {
        script: 'x',
        window,
      }),
    ).toThrow(/not authorized/);
    expect(() =>
      summarizeUsage({ data: { viewer: { accounts: [] } } }, namespaces, {
        script: 'x',
        window,
      }),
    ).toThrow(/no account data/);
  });
});

import { describe, expect, it } from 'vitest';
// Pure accounting helper: importing it never fetches billing or sends a notification.
import { assessCosts } from '../scripts/cost-report.mjs';

describe('migration spending warning', () => {
  const now = Date.parse('2026-10-12T12:00:00Z');
  const snapshot = {
    phase: 'migration',
    source: 'Project-attributed billing statement',
    observedAt: '2026-10-12T10:00:00Z',
    actualMigrationUsd: 50,
    monthlyEstimateUsd: 7,
  };
  it('warns strictly above $50 and keeps actual migration cost separate from a monthly estimate', () => {
    expect(assessCosts(snapshot, now).warning).toBe(false);
    expect(assessCosts({ ...snapshot, actualMigrationUsd: 50.01 }, now)).toMatchObject({
      warning: true,
      actualMigrationUsd: 50.01,
      monthlyEstimateUsd: 7,
    });
  });
  it('reports missing and stale billing explicitly and does not mistake planned migration for an active monitor', () => {
    expect(assessCosts({ ...snapshot, phase: 'planned' }, now).status).toBe('not_started');
    expect(assessCosts({ ...snapshot, actualMigrationUsd: null }, now).status).toBe('unavailable');
    expect(assessCosts({ ...snapshot, observedAt: '2026-10-09T00:00:00Z' }, now).status).toBe(
      'stale',
    );
  });
});

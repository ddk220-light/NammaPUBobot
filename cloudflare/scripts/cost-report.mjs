import { readFile } from 'node:fs/promises';

export function assessCosts(snapshot, now = Date.now()) {
  const warnAt = 50;
  if (snapshot.phase !== 'migration')
    return {
      status: 'not_started',
      warning: false,
      message:
        'Migration has not started. Set a dated project billing baseline before starting it.',
    };
  const actual = snapshot.actualMigrationUsd;
  if (
    actual === null ||
    !Number.isFinite(actual) ||
    actual < 0 ||
    !snapshot.source ||
    !Number.isFinite(Date.parse(snapshot.observedAt))
  )
    return {
      status: 'unavailable',
      warning: false,
      message: 'Actual migration spend is unavailable; this is not zero spend.',
    };
  if (Date.parse(snapshot.observedAt) > now + 60000)
    throw new Error('The billing observation is in the future.');
  const stale = now - Date.parse(snapshot.observedAt) > 172800000;
  return {
    status: stale ? 'stale' : 'current',
    warning: actual > warnAt,
    actualMigrationUsd: actual,
    monthlyEstimateUsd: snapshot.monthlyEstimateUsd ?? null,
    observedAt: snapshot.observedAt,
    message: `${actual > warnAt ? 'WARNING: Initial migration spend exceeds US$50. Review costs; do not stop accepted games.' : 'Initial migration spend is within the warning threshold.'}${stale ? ' Billing data is over 48 hours old.' : ''}`,
  };
}
if (process.argv[1]?.endsWith('cost-report.mjs')) {
  const path = process.argv[2];
  if (!path)
    throw new Error(
      'Provide a project-attributed billing snapshot JSON path; see ops/migration-budget.example.json.',
    );
  const result = assessCosts(JSON.parse(await readFile(path, 'utf8')));
  console.log(JSON.stringify(result, null, 2));
  if (result.warning) process.exitCode = 2;
}

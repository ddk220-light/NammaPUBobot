// Usage from Cloudflare's own analytics for one Worker and its Durable Objects.
// Pure helpers are exported for tests; only the CLI block below touches the network.
export const PAID_INCLUDED = {
  workerRequests: 10_000_000,
  workerCpuMs: 30_000_000,
  doRequests: 1_000_000,
  doDurationGbs: 400_000,
  doRowsRead: 25_000_000_000,
  doRowsWritten: 50_000_000,
  doStorageBytes: 5 * 1024 ** 3,
};
export const WARN_FRACTION = 0.8;
const DAY = 86400000;

export function usageWindow(days, now = Date.now()) {
  if (!Number.isInteger(days) || days < 1 || days > 31)
    throw new Error('Choose between 1 and 31 days; analytics queries cover one month at most.');
  const end = new Date(now).toISOString().slice(0, 10);
  const start = new Date(Date.parse(end) - (days - 1) * DAY).toISOString().slice(0, 10);
  return { start, end, days };
}

/** One GraphQL request using the dataset and field names verified against the live API. */
export function buildUsageQuery(accountId, window) {
  if (!/^[a-f0-9]{32}$/.test(accountId ?? ''))
    throw new Error('CLOUDFLARE_ACCOUNT_ID must be a 32-character account ID.');
  return {
    query: `query($a:String!,$s:Date!,$e:Date!){viewer{accounts(filter:{accountTag:$a}){workersInvocationsAdaptive(limit:10000,filter:{date_geq:$s,date_leq:$e},orderBy:[date_ASC]){dimensions{date scriptName status} sum{requests subrequests errors duration wallTime cpuTimeUs responseBodySize} quantiles{cpuTimeP50 cpuTimeP99 wallTimeP50 wallTimeP99}} durableObjectsInvocationsAdaptiveGroups(limit:10000,filter:{date_geq:$s,date_leq:$e}){dimensions{scriptName namespaceId type status} sum{requests errors wallTime responseBodySize} quantiles{wallTimeP50 wallTimeP99}} durableObjectsPeriodicGroups(limit:10000,filter:{date_geq:$s,date_leq:$e}){dimensions{namespaceId} sum{activeTime cpuTime duration exceededCpuErrors exceededMemoryErrors fatalInternalErrors inboundWebsocketMsgCount outboundWebsocketMsgCount rowsRead rowsWritten storageDeletes storageReadUnits storageWriteUnits subrequests}} durableObjectsSqlStorageGroups(limit:10000,filter:{date_geq:$s,date_leq:$e}){dimensions{namespaceId date} max{storedBytes}}}}}`,
    variables: { a: accountId, s: window.start, e: window.end },
  };
}

const sum = (rows, pick) => rows.reduce((n, r) => n + (Number(pick(r)) || 0), 0);

export function summarizeUsage(response, namespaces, { script, window }) {
  if (response?.errors?.length)
    throw new Error(`Analytics query failed: ${response.errors.map((e) => e.message).join('; ')}`);
  const account = response?.data?.viewer?.accounts?.[0];
  if (!account) throw new Error('Analytics returned no account data; check the account ID.');
  const owned = (namespaces?.result ?? []).filter((n) => n.script === script);
  const ids = new Set(owned.map((n) => n.id));
  const workers = (account.workersInvocationsAdaptive ?? []).filter(
    (r) => r.dimensions.scriptName === script,
  );
  const invocations = (account.durableObjectsInvocationsAdaptiveGroups ?? []).filter(
    (r) => r.dimensions.scriptName === script || ids.has(r.dimensions.namespaceId),
  );
  const periodic = (account.durableObjectsPeriodicGroups ?? []).filter((r) =>
    ids.has(r.dimensions.namespaceId),
  );
  const latest = new Map();
  for (const r of account.durableObjectsSqlStorageGroups ?? [])
    if (ids.has(r.dimensions.namespaceId)) {
      const seen = latest.get(r.dimensions.namespaceId);
      if (!seen || r.dimensions.date > seen.date)
        latest.set(r.dimensions.namespaceId, { date: r.dimensions.date, bytes: r.max.storedBytes });
    }
  const byType = {};
  for (const r of invocations)
    byType[r.dimensions.type] = (byType[r.dimensions.type] ?? 0) + (r.sum.requests ?? 0);
  const perNamespace = owned
    .map((n) => {
      const rows = periodic.filter((r) => r.dimensions.namespaceId === n.id);
      return {
        class: n.class,
        requests: sum(
          invocations.filter((r) => r.dimensions.namespaceId === n.id),
          (r) => r.sum.requests,
        ),
        durationGbs: sum(rows, (r) => r.sum.duration),
        storedBytes: latest.get(n.id)?.bytes ?? 0,
      };
    })
    .sort((a, b) => b.durationGbs - a.durationGbs);
  const totals = {
    workerRequests: sum(workers, (r) => r.sum.requests),
    workerErrors: sum(workers, (r) => r.sum.errors),
    workerSubrequests: sum(workers, (r) => r.sum.subrequests),
    workerCpuMs: sum(workers, (r) => r.sum.cpuTimeUs) / 1000,
    workerDurationGbs: sum(workers, (r) => r.sum.duration),
    doRequests: sum(invocations, (r) => r.sum.requests),
    doErrors: sum(invocations, (r) => r.sum.errors),
    doCpuMs: sum(periodic, (r) => r.sum.cpuTime) / 1000,
    doDurationGbs: sum(periodic, (r) => r.sum.duration),
    doRowsRead: sum(periodic, (r) => r.sum.rowsRead),
    doRowsWritten: sum(periodic, (r) => r.sum.rowsWritten),
    doStorageBytes: [...latest.values()].reduce((n, v) => n + v.bytes, 0),
  };
  const monthly = (value) => (value / window.days) * 30;
  const allowances = Object.entries(PAID_INCLUDED).map(([metric, included]) => {
    // Storage is a level, not a flow: compare the latest size, not a projection.
    const projected = metric === 'doStorageBytes' ? totals[metric] : monthly(totals[metric]);
    return { metric, observed: totals[metric], projected30Days: projected, included };
  });
  const warnings = allowances
    .filter((a) => a.projected30Days >= a.included * WARN_FRACTION)
    .map(
      (a) =>
        `${a.metric} projects to ${Math.round(a.projected30Days).toLocaleString('en-US')} over 30 days, ${Math.round((100 * a.projected30Days) / a.included)}% of the ${a.included.toLocaleString('en-US')} included in the Workers Paid plan.`,
    );
  if (!workers.length && !invocations.length)
    warnings.push(`No traffic was recorded for Worker "${script}". Check the Worker name.`);
  return {
    script,
    window,
    totals,
    errorRate: totals.workerRequests ? totals.workerErrors / totals.workerRequests : 0,
    invocationsByType: byType,
    namespaces: perNamespace,
    allowances,
    warnings,
  };
}

const fmt = (n, digits = 0) => Number(n).toLocaleString('en-US', { maximumFractionDigits: digits });
export function formatUsage(s) {
  const t = s.totals;
  return [
    `Usage for ${s.script}, ${s.window.start} to ${s.window.end} (${s.window.days} days, UTC)`,
    '',
    `Worker requests        ${fmt(t.workerRequests)} (${fmt(t.workerErrors)} errors, ${(100 * s.errorRate).toFixed(2)}%)`,
    `Worker CPU             ${fmt(t.workerCpuMs)} ms`,
    `Worker subrequests     ${fmt(t.workerSubrequests)}`,
    `Durable Object calls   ${fmt(t.doRequests)} (${fmt(t.doErrors)} errors) ${Object.entries(
      s.invocationsByType,
    )
      .map(([k, v]) => `${k}: ${fmt(v)}`)
      .join(', ')}`,
    `Durable Object time    ${fmt(t.doDurationGbs, 1)} GB-s (billed duration)`,
    `Durable Object rows    ${fmt(t.doRowsRead)} read, ${fmt(t.doRowsWritten)} written`,
    `Durable Object storage ${fmt(t.doStorageBytes / 1024 ** 2, 1)} MiB (latest day)`,
    '',
    'By class:',
    ...s.namespaces.map(
      (n) =>
        `  ${n.class.padEnd(22)} ${fmt(n.requests).padStart(10)} calls ${fmt(n.durationGbs, 1).padStart(12)} GB-s ${fmt(n.storedBytes / 1024 ** 2, 1).padStart(8)} MiB`,
    ),
    '',
    'Against the Workers Paid included usage (30-day projection):',
    ...s.allowances.map(
      (a) =>
        `  ${a.metric.padEnd(16)} ${fmt(a.projected30Days).padStart(16)} of ${fmt(a.included).padStart(16)} (${fmt((100 * a.projected30Days) / a.included, 1)}%)`,
    ),
    '',
    ...(s.warnings.length ? s.warnings.map((w) => `WARNING: ${w}`) : ['No usage warnings.']),
  ].join('\n');
}

if (process.argv[1]?.endsWith('usage-report.mjs')) {
  const args = process.argv.slice(2);
  const option = (name) => {
    const i = args.indexOf(`--${name}`);
    return i >= 0 ? args[i + 1] : undefined;
  };
  const script = option('worker');
  if (!script)
    throw new Error(
      'Usage: npm run usage:report -- --worker WORKER_NAME [--days 30] [--json]\nSet CLOUDFLARE_API_TOKEN (Account Analytics: Read, Workers Scripts: Read) and CLOUDFLARE_ACCOUNT_ID.',
    );
  const token = process.env.CLOUDFLARE_API_TOKEN,
    accountId = process.env.CLOUDFLARE_ACCOUNT_ID;
  if (!token || !accountId)
    throw new Error('Set CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID in the environment.');
  const window = usageWindow(Number(option('days') ?? 30));
  const api = 'https://api.cloudflare.com/client/v4';
  const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' };
  const request = async (url, init) => {
    const response = await fetch(url, { headers, signal: AbortSignal.timeout(30000), ...init });
    if (!response.ok) throw new Error(`Cloudflare returned HTTP ${response.status} for ${url}.`);
    return response.json();
  };
  const namespaces = await request(
    `${api}/accounts/${accountId}/workers/durable_objects/namespaces?per_page=1000`,
  );
  const analytics = await request(`${api}/graphql`, {
    method: 'POST',
    body: JSON.stringify(buildUsageQuery(accountId, window)),
  });
  const summary = summarizeUsage(analytics, namespaces, { script, window });
  console.log(args.includes('--json') ? JSON.stringify(summary, null, 2) : formatUsage(summary));
  if (summary.warnings.length) process.exitCode = 2;
}

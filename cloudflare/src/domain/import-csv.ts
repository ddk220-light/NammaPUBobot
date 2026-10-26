import { importSchema, type ImportBundle } from './records';
// RFC4180 quoted fields, including commas and embedded newlines; no spreadsheet execution.
export function csvRows(text: string): string[][] {
  const out: string[][] = [],
    row: string[] = [];
  let field = '',
    quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (c === '"') {
      if (quoted && text[i + 1] === '"') {
        field += '"';
        i++;
      } else if (quoted || !field) quoted = !quoted;
      else throw new Error('Malformed CSV quoting.');
    } else if (!quoted && (c === ',' || c === '\n' || c === '\r')) {
      row.push(field);
      field = '';
      if (c !== ',') {
        if (c === '\r' && text[i + 1] === '\n') i++;
        if (row.some(Boolean)) out.push(row.splice(0));
        else row.length = 0;
      }
    } else field += c;
  }
  if (quoted) throw new Error('CSV has an unclosed quote.');
  row.push(field);
  if (row.some(Boolean)) out.push(row);
  return out;
}
export function seedCsv(
  text: string,
  gameId: string,
  queueId: string,
  sourceId: string,
  source: 'manual' | 'csv' = 'csv',
): ImportBundle {
  const rows = csvRows(text.replace(/^\uFEFF/, '')),
    header = rows.shift() ?? [];
  if (!['id', 'name', 'rating', 'deviation'].every((k) => header.includes(k)))
    throw new Error(
      'CSV needs id,name,rating,deviation headers. Optional: wins,losses,draws,streak,hidden,lastRankedAt.',
    );
  const players = rows.map((r, i) => {
    if (r.length !== header.length)
      throw new Error(`CSV row ${i + 2} has the wrong number of columns.`);
    const row = Object.fromEntries(header.map((k, n) => [k.trim(), r[n].trim()]));
    if (!['id', 'name', 'rating', 'deviation'].every((k) => row[k]))
      throw new Error(`CSV row ${i + 2} needs an ID, name, rating and deviation.`);
    if (row.lastRankedAt && !/(Z|[+-]\d\d:\d\d)$/.test(row.lastRankedAt))
      throw new Error('Use an ISO timestamp with a timezone for lastRankedAt.');
    return {
      ladder: 'default',
      player: {
        id: row.id,
        name: row.name,
        rating: Number(row.rating),
        deviation: Number(row.deviation),
        wins: Number(row.wins ?? 0),
        losses: Number(row.losses ?? 0),
        draws: Number(row.draws ?? 0),
        streak: Number(row.streak ?? 0),
      },
      hidden: ['1', 'true'].includes(row.hidden ?? ''),
      lastRankedAt: row.lastRankedAt ? Date.parse(row.lastRankedAt) : null,
    };
  });
  return importSchema.parse({
    version: 1,
    source,
    sourceId,
    mode: 'seed',
    mappings: [{ ladder: 'default', gameId, queues: { default: queueId } }],
    players,
    matches: [],
    changes: [],
    unsupported: [],
  });
}

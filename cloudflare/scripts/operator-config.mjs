import { readFile, writeFile } from 'node:fs/promises';
import { parse } from 'jsonc-parser';
const [name, origin, file = 'wrangler.operator.json'] = process.argv.slice(2);
if (!/^[a-z][a-z0-9-]{2,50}$/.test(name ?? '') || new URL(origin).protocol !== 'https:')
  throw Error('Provide a unique Worker name and HTTPS origin.');
const config = parse(await readFile('wrangler.jsonc', 'utf8'));
delete config.env;
config.name = name;
config.vars = { APP_ENV: 'staging', APP_ORIGIN: origin };
await writeFile(file, JSON.stringify(config, null, 2) + '\n', { mode: 0o600 });
console.log(
  `Independent configuration written to ${file}; storage namespaces and secrets must be created in the operator account.`,
);

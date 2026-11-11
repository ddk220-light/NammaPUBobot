// Node-only: reads src/identity.ts so site content and checks share one source of truth.
import { readFile } from 'node:fs/promises';
import { transform } from 'esbuild';

const source = await readFile(new URL('../src/identity.ts', import.meta.url), 'utf8');
const { code } = await transform(source, { loader: 'ts', format: 'esm' });
export const { identity } = await import(
  `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`
);

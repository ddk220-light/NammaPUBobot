import { spawn } from 'node:child_process';

// Wrangler's event metadata may contain OAuth URLs/cookies. Forward only our
// structured application diagnostics; never print raw tail events.
const child = spawn(
  'node_modules/.bin/wrangler',
  ['tail', '--env', 'staging', '--format', 'json'],
  { stdio: ['ignore', 'pipe', 'pipe'] },
);
let buffer = '';
child.stdout.on('data', (bytes) => {
  buffer += bytes.toString();
  // Tail produces pretty-printed JSON objects; parse complete top-level objects.
  let start = -1,
    depth = 0,
    quoted = false,
    escaped = false,
    consumed = 0;
  for (let i = 0; i < buffer.length; i++) {
    const ch = buffer[i];
    if (start < 0) {
      if (ch !== '{') continue;
      start = i;
      depth = 1;
      continue;
    }
    if (quoted) {
      if (escaped) escaped = false;
      else if (ch === '\\') escaped = true;
      else if (ch === '"') quoted = false;
      continue;
    }
    if (ch === '"') quoted = true;
    else if (ch === '{') depth++;
    else if (ch === '}' && --depth === 0) {
      try {
        const event = JSON.parse(buffer.slice(start, i + 1));
        for (const log of event.logs ?? [])
          for (const message of log.message ?? []) {
            try {
              const item = JSON.parse(message);
              if (typeof item.requestId !== 'string' || typeof item.path !== 'string') continue;
              console.log(
                JSON.stringify(
                  Object.fromEntries(
                    [
                      'requestId',
                      'path',
                      'method',
                      'status',
                      'elapsedMs',
                      'code',
                      'providerStatus',
                      'retryAfter',
                    ]
                      .filter((k) => item[k] !== undefined)
                      .map((k) => [k, item[k]]),
                  ),
                ),
              );
            } catch {}
          }
      } catch {}
      consumed = i + 1;
      start = -1;
    }
  }
  if (consumed) buffer = buffer.slice(consumed);
  if (buffer.length > 1048576) buffer = '';
});
child.stderr.on('data', () => {});
process.on('SIGINT', () => child.kill('SIGINT'));
child.on('exit', (code) => process.exit(code ?? 0));

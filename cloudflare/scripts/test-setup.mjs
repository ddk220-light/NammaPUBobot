import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

// The generated-site tests read the built pages through the ASSETS binding, so a clean
// checkout needs the site built before the Worker pool starts, and a stale build would lie.
export default function setup() {
  execFileSync('npm', ['run', 'build:web'], {
    cwd: fileURLToPath(new URL('../', import.meta.url)),
    stdio: ['ignore', 'ignore', 'inherit'],
  });
}

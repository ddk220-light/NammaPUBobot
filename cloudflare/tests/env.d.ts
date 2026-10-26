import type { Env as AppEnv } from '../src/env';
declare global {
  namespace Cloudflare {
    interface Env extends AppEnv {}
    interface GlobalProps {
      mainModule: typeof import('../src/worker');
    }
  }
}
export {};

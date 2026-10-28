import { cloudflareTest } from '@cloudflare/vitest-plugin';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [
    cloudflareTest({
      wrangler: { configPath: './wrangler.jsonc' },
      remoteBindings: false,
      miniflare: {
        bindings: {
          APP_ENV: 'staging',
          // Deterministic lifecycle fixtures exercise the explicitly enabled AoE2 path.
          // Deployment vars deliberately contain no acceptance approval.
          CONNECTOR_ACCEPTED_GAMES: 'aoe2',
          APP_ORIGIN: 'https://setup.example',
          DISCORD_APPLICATION_ID: '100000000000000001',
          // Public RFC 8032 test vector; this is not a deployed application's key.
          DISCORD_PUBLIC_KEY: 'd75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a',
          DISCORD_CLIENT_SECRET: 'test-client-secret',
          DISCORD_BOT_TOKEN: 'test-bot-token',
        },
      },
    }),
  ],
  test: { include: ['tests/**/*.test.ts'], fileParallelism: false },
});

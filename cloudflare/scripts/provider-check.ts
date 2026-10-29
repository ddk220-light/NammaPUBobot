import { selectableCatalog } from '../src/games/catalog';
import { catalogIds, type CatalogId } from '../src/games/types';
import { gameAdapter } from '../src/games/registry';
import { providerJson } from '../src/games/transport';
import { runProviderChecks } from './provider-check-core';
import { probeAoE2Feed, probeAoE2Ongoing } from './provider-check-aoe2';
import { battleNetClientToken, GameAccountError } from '../src/auth/game-accounts';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';

export async function main(
  args: string[],
  env: Record<string, string | undefined>,
): Promise<number> {
  let games: CatalogId[] = selectableCatalog.map((game) => game.id);
  let output: string | undefined;
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--game') {
      const ids = args[++i]?.split(',') ?? [];
      if (!ids.length || ids.some((id) => !catalogIds.includes(id as CatalogId)))
        throw Error('Unknown game. Use a catalog ID.');
      games = [...new Set(ids)] as CatalogId[];
    } else if (args[i] === '--output' && args[i + 1]) output = args[++i];
    else if (args[i] === '--help') {
      process.stdout.write(
        'Usage: npm run provider:check -- [--game aoe2,aoe3,aoe4,aom,bar,zero_hour,wc3] [--output dist/provider-check.json]\nRead-only metadata. Interactive sign-in and real-game acceptance are excluded.\n',
      );
      return 0;
    } else throw Error('Unknown argument. Use --help.');
  }
  // A service grant permits public API reads only; it is never ownership evidence.
  const probeEnv = { ...env };
  if (
    games.includes('sc2') &&
    !probeEnv.BATTLE_NET_ACCESS_TOKEN &&
    probeEnv.BATTLENET_CLIENT_ID &&
    probeEnv.BATTLENET_CLIENT_SECRET
  ) {
    try {
      probeEnv.BATTLE_NET_ACCESS_TOKEN = (
        await battleNetClientToken({
          BATTLENET_CLIENT_ID: probeEnv.BATTLENET_CLIENT_ID,
          BATTLENET_CLIENT_SECRET: probeEnv.BATTLENET_CLIENT_SECRET,
        })
      ).accessToken;
    } catch (error) {
      probeEnv.CONNECTOR_PROBE_SC2_AUTH_ERROR =
        error instanceof GameAccountError ? error.code : 'provider_unavailable';
    }
  }
  const report = await runProviderChecks({
    games,
    createAdapter: gameAdapter,
    request: providerJson,
    env: probeEnv,
    discoverAoE2: probeAoE2Feed,
    discoverAoE2Ongoing: probeAoE2Ongoing,
  });
  const json = `${JSON.stringify(report, null, 2)}\n`;
  if (output) {
    await mkdir(path.dirname(output), { recursive: true });
    await writeFile(output, json, { mode: 0o600 });
    process.stdout.write(
      `${JSON.stringify({ checkedAt: report.checkedAt, scope: report.scope, counts: report.counts })}\n`,
    );
  } else process.stdout.write(json);
  return report.counts.failed ? 1 : 0;
}

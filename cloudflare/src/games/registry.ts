import type { GameAdapter } from './types';
import { aoe2Adapter } from './aoe2';
const adapters: Readonly<Record<string, GameAdapter>> = { aoe2: aoe2Adapter };
export function gameAdapter(catalogId: string | null | undefined): GameAdapter | undefined {
  return catalogId ? adapters[catalogId] : undefined;
}

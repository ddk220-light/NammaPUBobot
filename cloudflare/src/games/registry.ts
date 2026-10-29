import type { AdapterRuntime, CatalogId, GameAdapter } from './types';
import { createWc3Adapter } from './wc3';
import { createAoE2Adapter } from './aoe2';
import { createAoE3Adapter } from './aoe3';
import { createAoE4Adapter } from './aoe4';
import { createAoMAdapter } from './aom';
import { createBarAdapter } from './bar';
import { createGeneralsOnlineAdapter } from './generals-online';
import { createSc2Adapter } from './sc2';
import { createManualAdapter } from './manual';

/** Runtime credentials/transport are scoped to one caller, never process-global mutable state. */
const factories: Record<CatalogId, (runtime: AdapterRuntime) => GameAdapter> = {
  wc3: createWc3Adapter,
  aoe2: createAoE2Adapter,
  aoe3: createAoE3Adapter,
  aoe4: createAoE4Adapter,
  aom: createAoMAdapter,
  bar: createBarAdapter,
  zero_hour: createGeneralsOnlineAdapter,
  sc2: createSc2Adapter,
  generals: () => createManualAdapter('generals'),
  northgard: () => createManualAdapter('northgard'),
  tempest_rising: () => createManualAdapter('tempest_rising'),
};
export function gameAdapter(
  catalogId: string | null | undefined,
  runtime: AdapterRuntime = {},
): GameAdapter | undefined {
  return catalogId && Object.hasOwn(factories, catalogId)
    ? factories[catalogId as CatalogId](runtime)
    : undefined;
}

import { createAgeAdapter, type AgeConfiguration } from './worlds-edge';
import type { AdapterRuntime } from './types';
export const aomConfiguration: AgeConfiguration = {
  id: 'aom',
  title: 'athens',
  host: 'https://athens-live-api.worldsedgelink.com',
  edition: 'Age of Mythology: Retold',
  selectionLabel: 'God',
  simulationCounter: false,
};
export const createAoMAdapter = (runtime: AdapterRuntime = {}) =>
  createAgeAdapter(aomConfiguration, runtime);

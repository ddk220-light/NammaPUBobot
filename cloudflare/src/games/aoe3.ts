import { createAgeAdapter, type AgeConfiguration } from './worlds-edge';
import type { AdapterRuntime } from './types';
export const aoe3Configuration: AgeConfiguration = {
  id: 'aoe3',
  title: 'age3',
  host: 'https://aoe-api.worldsedgelink.com',
  edition: 'Age of Empires III: Definitive Edition',
  selectionLabel: 'Civilization',
  simulationCounter: false,
};
export const createAoE3Adapter = (runtime: AdapterRuntime = {}) =>
  createAgeAdapter(aoe3Configuration, runtime);

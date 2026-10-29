/** W3Champions Race (bit flags), not the separate replay RaceId enum. */
export const wc3CatalogVersion = '2026-10-29.1';
export const wc3Races: Readonly<Record<string, string>> = {
  '0': 'Random',
  '1': 'Human',
  '2': 'Orc',
  '4': 'Night Elf',
  '8': 'Undead',
};

/** Only these melee ladders have captured ongoing and completed provider schemas. */
export const wc3SupportedModes: Readonly<Record<string, { name: string; teamSize: number }>> = {
  '1': { name: '1v1', teamSize: 1 },
  '4': { name: '4v4', teamSize: 4 },
};

export function wc3RaceSelection(race: unknown, randomRace: unknown) {
  const id = (value: unknown): string => {
    if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0)
      throw Error('Invalid W3Champions race');
    return String(value);
  };
  const selected = id(race);
  const actual = randomRace === null || randomRace === undefined ? undefined : id(randomRace);
  const name = (value: string) => wc3Races[value] ?? `Unknown race (${value})`;
  return {
    selectionId: selected,
    selection:
      selected === '0' && actual !== undefined && actual !== '0'
        ? `Random (${name(actual)})`
        : name(selected),
  };
}

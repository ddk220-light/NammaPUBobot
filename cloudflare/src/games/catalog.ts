/** Capabilities shipped by the adapters, not promises about other games. */
export const gameCatalog = [
  {
    id: 'aoe2',
    name: 'Age of Empires II',
    selection: 'civilization',
    lobby: true,
    occupancy: true,
    profileProvider: 'aoe2',
  },
  {
    id: 'aoe3',
    name: 'Age of Empires III',
    selection: 'civilization',
    lobby: false,
    occupancy: false,
    profileProvider: null,
  },
  {
    id: 'aoe4',
    name: 'Age of Empires IV',
    selection: 'civilization',
    lobby: false,
    occupancy: false,
    profileProvider: null,
  },
  {
    id: 'aom',
    name: 'Age of Mythology',
    selection: 'god',
    lobby: false,
    occupancy: false,
    profileProvider: null,
  },
  {
    id: 'generals',
    name: 'Command & Conquer Generals',
    selection: 'faction',
    lobby: false,
    occupancy: false,
    profileProvider: null,
  },
] as const;
export const gameCapabilities = (id: string | null) => gameCatalog.find((game) => game.id === id);

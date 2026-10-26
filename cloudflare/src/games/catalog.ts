/** Draft capabilities, not wired to setup or the server yet. `lobby` describes adapter intent, not feature availability. */
export const gameCatalog = [
  {
    id: 'aoe2',
    name: 'Age of Empires II',
    selection: 'civilization',
    lobby: true,
    profileProvider: 'aoe2',
  },
  {
    id: 'aoe3',
    name: 'Age of Empires III',
    selection: 'civilization',
    lobby: false,
    profileProvider: null,
  },
  {
    id: 'aoe4',
    name: 'Age of Empires IV',
    selection: 'civilization',
    lobby: false,
    profileProvider: null,
  },
  { id: 'aom', name: 'Age of Mythology', selection: 'god', lobby: false, profileProvider: null },
  {
    id: 'generals',
    name: 'Command & Conquer Generals',
    selection: 'faction',
    lobby: false,
    profileProvider: null,
  },
] as const;
export const gameCapabilities = (id: string | null) => gameCatalog.find((game) => game.id === id);

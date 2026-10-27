/** Per-game selection catalogs. Picking is optional and never claims what was played. */
export interface SelectionCatalog {
  label: string;
  base: string[];
  paid: string[];
}
export const aoe2Civs: SelectionCatalog = {
  label: 'Civilization',
  base: 'Aztecs,Bengalis,Berbers,Bohemians,Britons,Bulgarians,Burgundians,Burmese,Byzantines,Celts,Chinese,Cumans,Dravidians,Ethiopians,Franks,Goths,Gurjaras,Hindustanis,Huns,Incas,Italians,Japanese,Khmer,Koreans,Lithuanians,Magyars,Malay,Malians,Mayans,Mongols,Persians,Poles,Portuguese,Saracens,Sicilians,Slavs,Spanish,Tatars,Teutons,Turks,Vietnamese,Vikings'.split(
    ',',
  ),
  paid: 'Armenians,Georgians,Jurchens,Khitan,Mapuche,Muisca,Romans,Shu,Tupi,Wei,Wu,Danes,Saxons,Varangians'.split(
    ',',
  ),
};
export const selectionCatalog = (id?: string | null) => (id === 'aoe2' ? aoe2Civs : undefined);
export function selectionPool(
  catalog: SelectionCatalog,
  history: { selection: string; at: number }[],
  random = Math.random,
) {
  const shuffle = (xs: string[]) => {
    const a = [...xs];
    for (let i = a.length - 1; i > 0; i--) {
      const j = Math.floor(random() * (i + 1));
      [a[i], a[j]] = [a[j], a[i]];
    }
    return a;
  };
  const score = (s: string) => {
    const rows = history.filter((h) => h.selection === s);
    return [rows.length, Math.max(0, ...rows.map((h) => h.at))];
  };
  const choose = (xs: string[], n: number) =>
    shuffle(xs)
      .sort((a, b) => {
        const x = score(a),
          y = score(b);
        return x[0] - y[0] || x[1] - y[1];
      })
      .slice(0, n);
  return shuffle([...choose(catalog.base, 8), ...choose(catalog.paid, 4)]);
}

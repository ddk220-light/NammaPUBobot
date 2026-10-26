import { z } from 'zod';
import { rawChanges } from './rating-models';

export const ratingSettingsSchema = z
  .object({
    initial: z.number().int().min(0).max(10000),
    deviation: z.number().int().min(1).max(3000),
    minDeviation: z.number().int().min(0).max(3000),
    scale: z.number().min(0).max(1000),
    winScale: z.number().min(0).max(500),
    lossScale: z.number().min(0).max(500),
    drawBonus: z.number().min(-500).max(500),
    winStreakBoost: z.boolean(),
    lossStreakBoost: z.boolean(),
  })
  .strict();
export type RatingSettings = z.infer<typeof ratingSettingsSchema>;
export const defaultRatingSettings: RatingSettings = {
  initial: 1000,
  deviation: 200,
  minDeviation: 100,
  scale: 100,
  winScale: 100,
  lossScale: 100,
  drawBonus: 0,
  winStreakBoost: false,
  lossStreakBoost: false,
};
export interface Player {
  id: string;
  name: string;
  rating: number;
  deviation: number;
  wins: number;
  losses: number;
  draws: number;
  streak: number;
}
// Python round() uses ties-to-even. Math.round() would change historical Elo results.
export function roundEven(value: number) {
  const floor = Math.floor(value),
    remainder = value - floor;
  return remainder === 0.5 ? floor + (floor % 2 ? 1 : 0) : Math.round(value);
}
export function rateTeams(
  teams: [Player[], Player[]],
  winner: 0 | 1 | null,
  settings: RatingSettings,
  engine: 'aoe2' | 'flat' | 'glicko2' | 'trueskill' = 'aoe2',
): [Player[], Player[]] {
  const changes = rawChanges(teams, winner, settings, engine);
  return teams.map((team, side) =>
    team.map((player, index) => {
      const p = { ...player };
      const score = winner === null ? 0 : winner === side ? 1 : -1;
      let change = changes[side][index].rating;
      // The old bot treats zero scale values as the default 100%.
      if (score === 0) {
        change =
          engine === 'flat'
            ? (10 * settings.drawBonus) / 100
            : change + (Math.abs(change) * settings.drawBonus) / 100;
        p.draws++;
        p.streak = 0;
      } else if (score === 1) {
        change *= (settings.winScale || 100) / 100;
        p.wins++;
        p.streak = p.streak <= 0 ? 1 : p.streak + 1;
        if (settings.winStreakBoost && p.streak > 2) change *= Math.min(p.streak, 6) / 2;
      } else {
        change *= (settings.lossScale || 100) / 100;
        p.losses++;
        p.streak = p.streak >= 0 ? -1 : p.streak - 1;
        if (settings.lossStreakBoost && p.streak < -2)
          change *= Math.min(Math.abs(p.streak), 6) / 2;
      }
      change *= (settings.scale || 100) / 100;
      p.rating = Math.max(0, roundEven(p.rating + change));
      p.deviation = Math.max(
        settings.minDeviation,
        roundEven(p.deviation + changes[side][index].deviation),
      );
      if (!Number.isFinite(p.rating) || !Number.isFinite(p.deviation))
        throw new Error(
          'Rating calculation could not converge. Adjust rating parameters before reporting.',
        );
      return p;
    }),
  ) as [Player[], Player[]];
}

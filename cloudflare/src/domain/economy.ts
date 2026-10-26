export const WEEK = 7 * 86400000;
export const GOLD_FLOOR = 500;
export function stakeOptions(balance: number): number[] {
  if (balance < 10) return [];
  const reference = Math.min(500 * Math.ceil(balance / 500), 2 * balance);
  return [
    ...new Set([10, 4, 2].map((d) => Math.min(balance, Math.max(10, Math.floor(reference / d))))),
  ].sort((a, b) => a - b);
}
export function taxAmount(
  balance: number,
  seededAt: number,
  cutoff: number,
  participated: boolean,
) {
  return balance <= 500 || participated || seededAt > cutoff - WEEK
    ? 0
    : Math.min(Math.floor(balance / 10), balance - 500);
}
/** Thursday at the configured UTC minute. */
export function nextThursday(now: number, minute = 0) {
  const date = new Date(now),
    day = date.getUTCDay();
  const midnight = Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate());
  let due = midnight + ((4 - day + 7) % 7) * 86400000 + minute * 60000;
  if (due <= now) due += WEEK;
  return due;
}
export type Book = {
  id: string;
  match_id: string;
  game_id: string;
  queue_id: string;
  round: number;
  status: 'open' | 'frozen' | 'settled' | 'refunded';
  opened_at: number;
  closed_at: number | null;
  result: 'alpha' | 'beta' | 'draw' | 'abort' | null;
  reason: string | null;
  burned: number;
};
export type Bet = { user_id: string; side: 'alpha' | 'beta'; stake: number };

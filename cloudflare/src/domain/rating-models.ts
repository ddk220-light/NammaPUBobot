import type { Player, RatingSettings } from './rating';

type Change = { rating: number; deviation: number };
const average = (team: Player[], key: 'rating' | 'deviation') =>
  team.reduce((s, p) => s + p[key], 0) / team.length;
const pdf = (x: number) => Math.exp((-x * x) / 2) / Math.sqrt(2 * Math.PI);
// Normal tail evaluated directly avoids cancellation for an unexpected winner.
function erfc(x: number): number {
  const z = Math.abs(x),
    t = 1 / (1 + z / 2);
  const result =
    t *
    Math.exp(
      -z * z -
        1.26551223 +
        t *
          (1.00002368 +
            t *
              (0.37409196 +
                t *
                  (0.09678418 +
                    t *
                      (-0.18628806 +
                        t *
                          (0.27886807 +
                            t *
                              (-1.13520398 +
                                t * (1.48851587 + t * (-0.82215223 + t * 0.17087277)))))))),
    );
  return x >= 0 ? result : 2 - result;
}
const cdf = (x: number) => erfc(-x / Math.SQRT2) / 2;
function quantile(p: number) {
  let low = -10,
    high = 10;
  for (let i = 0; i < 70; i++) {
    const mid = (low + high) / 2;
    if (cdf(mid) < p) low = mid;
    else high = mid;
  }
  return (low + high) / 2;
}
function glicko(
  rating: number,
  rd: number,
  opponent: number,
  opponentRd: number,
  score: number,
  vol: number,
) {
  const factor = 173.7178,
    mu = (rating - 1500) / factor,
    phi = rd / factor;
  const g = 1 / Math.sqrt(1 + (3 * (opponentRd / factor) ** 2) / Math.PI ** 2);
  const x = g * (mu - (opponent - 1500) / factor),
    e = 1 / (1 + Math.exp(-x)),
    complement = 1 / (1 + Math.exp(x));
  const residual = score === 1 ? complement : score === 0 ? -e : score - e;
  const v = 1 / (g * g * e * complement),
    delta = v * g * residual,
    a = Math.log(vol * vol),
    tau = 0.5;
  const f = (x: number) => {
    const ex = Math.exp(x);
    return (
      (ex * (delta * delta - phi * phi - v - ex)) / (2 * (phi * phi + v + ex) ** 2) -
      (x - a) / (tau * tau)
    );
  };
  let A = a,
    B: number;
  if (delta * delta > phi * phi + v) B = Math.log(delta * delta - phi * phi - v);
  else {
    let k = 1;
    while (f(a - k * tau) < 0 && k < 100) k++;
    B = a - k * tau;
  }
  let fa = f(A),
    fb = f(B);
  for (let i = 0; Math.abs(B - A) > 1e-6 && i < 100; i++) {
    const C = A + ((A - B) * fa) / (fb - fa),
      fc = f(C);
    if (fc * fb <= 0) {
      A = B;
      fa = fb;
    } else fa /= 2;
    B = C;
    fb = fc;
  }
  const nextVol = Math.exp(A / 2),
    prior = phi * phi + nextVol * nextVol,
    posterior = 1 / (1 / prior + 1 / v);
  return {
    rating: factor * posterior * g * residual,
    deviation: factor * Math.sqrt(posterior) - rd,
    vol: nextVol,
  };
}
export function rawChanges(
  teams: [Player[], Player[]],
  winner: 0 | 1 | null,
  s: RatingSettings,
  engine: string,
): [Change[], Change[]] {
  const changes: [Change[], Change[]] = [[], []];
  if (engine === 'aoe2' || engine === 'flat') {
    const k = Math.max(32 / Math.max(teams[0].length, teams[1].length), 4);
    return teams.map((team, side) =>
      team.map((p) => {
        const score = winner === null ? 0.5 : winner === side ? 1 : 0;
        const change =
          engine === 'flat'
            ? (score - 0.5) * 20
            : k *
              (score - 1 / (1 + 10 ** ((average(teams[1 - side], 'rating') - p.rating) / 400))) *
              (p.wins + p.losses + p.draws < 10 ? 3 : 1);
        return { rating: change, deviation: 0 };
      }),
    ) as [Change[], Change[]];
  }
  // Preserve the legacy winner-first traversal, including its per-call Glicko volatility carry.
  const order = winner === 1 ? [1, 0] : [0, 1];
  if (engine === 'glicko2') {
    let vol = 0.06;
    for (const side of order) {
      const own = Math.trunc(average(teams[side], 'rating')),
        opp = Math.trunc(average(teams[1 - side], 'rating')),
        oppRd = Math.trunc(average(teams[1 - side], 'deviation'));
      for (const p of teams[side]) {
        const change = glicko(
          own,
          p.deviation,
          opp,
          oppRd,
          winner === null ? 0.5 : winner === side ? 1 : 0,
          vol,
        );
        vol = change.vol;
        changes[side].push({ rating: change.rating, deviation: change.deviation });
      }
    }
    return changes;
  }
  if (engine !== 'trueskill') throw new Error('Unknown rating model.');
  const beta = Math.trunc(s.deviation / 2),
    tau = Math.trunc(s.deviation / 100),
    n = teams[0].length + teams[1].length;
  if (beta <= 0) throw new Error('TrueSkill needs an initial deviation of at least 2.');
  const variance = teams.map((t) => t.map((p) => p.deviation * p.deviation + tau * tau));
  const c = Math.sqrt(variance.flat().reduce((a, b) => a + b, 0) + n * beta * beta);
  const first = order[0],
    other = order[1];
  const difference =
    (teams[first].reduce((a, p) => a + p.rating, 0) -
      teams[other].reduce((a, p) => a + p.rating, 0)) /
    c;
  const margin = (quantile(0.55) * Math.sqrt(n) * beta) / c;
  let v: number, w: number;
  if (winner === null) {
    const abs = Math.abs(difference),
      a = margin - abs,
      b = -margin - abs,
      denominator = cdf(a) - cdf(b);
    if (denominator < 1e-300)
      throw new Error(
        'TrueSkill draw is too improbable for these parameters. Review the ratings/deviations.',
      );
    const positive = (pdf(b) - pdf(a)) / denominator;
    v = positive * (difference < 0 ? -1 : 1);
    w = positive * positive + (a * pdf(a) - b * pdf(b)) / denominator;
  } else {
    const x = difference - margin,
      denominator = cdf(x);
    v = denominator ? pdf(x) / denominator : -x;
    w = v * (v + x);
    if (w >= 1 || !Number.isFinite(w))
      throw new Error(
        'TrueSkill result is too improbable for these parameters. Review the ratings/deviations.',
      );
  }
  for (const side of order)
    changes[side] = teams[side].map((p, i) => ({
      rating: (((side === first ? 1 : -1) * variance[side][i]) / c) * v,
      deviation:
        Math.sqrt(Math.max(0, variance[side][i] * (1 - (variance[side][i] / (c * c)) * w))) -
        p.deviation,
    }));
  return changes;
}

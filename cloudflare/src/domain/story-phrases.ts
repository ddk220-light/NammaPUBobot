// Python round uses ties-to-even; preserve the legacy percentages.
const legacyRound = (n: number) => (n % 1 === 0.5 ? 2 * Math.round(n / 2) : Math.round(n));
// Phrase alternatives ported from features/storylines/{insights,payoff}.py.
import type { StoryCandidate } from './story-candidates';
export const joinNames = (xs: string[]) =>
  xs.length < 2 ? (xs[0] ?? '') : xs.slice(0, -1).join(', ') + ' & ' + xs.at(-1);

export function teasePhrase(
  c: StoryCandidate,
  nick: Record<string, string>,
  frame: string,
  choose: (xs: string[]) => string,
): string {
  const name = (uid: string) => `**${nick[uid] ?? 'someone'}**`;
  let d: StoryCandidate['data'],
    t: string,
    who: string,
    w: number,
    g: number,
    a: string,
    b: string,
    n: number,
    p: string,
    q: string,
    wr: number,
    base: number,
    winner: string,
    loser: string,
    k: number,
    series: number,
    each: number,
    opts: string[];
  d = c['data'];
  t = c['type'];
  if (t === 'lineup') {
    who = joinNames(d['ids'].map((u: string) => name(u)));
    [w, g] = [d['wins'], d['games']];
    if (d['one_way'] && w === g) {
      opts = [
        `🃏 Jackpot: this group has shared a side ${g} times and won every one — **${w}-0**. ${frame}`,
        `🎰 ${who} — the same ${d['ids'].length} that are **${w}-0** together. Lightning, twice. ${frame}`,
      ];
    } else {
      if (d['one_way']) {
        opts = [
          `🃏 This group has shared a side ${g} times and lost every one (**0-${g}**). ${frame}`,
          `🎰 ${who}, back for another go at a **0-${g}** record together. ${frame}`,
        ];
      } else {
        opts = [
          `🃏 Rare reunion: this group has only shared a side ${g} times before — **${w}-${g - w}**. ${frame}`,
          `🎰 ${who} ride again. Their shared record: **${w}-${g - w}**. ${frame}`,
        ];
      }
    }
    return choose(opts);
  }
  if (t === 'trio') {
    who = joinNames(d['ids'].map((u: string) => name(u)));
    [w, g] = [d['wins'], d['games']];
    if (d['won']) {
      opts = [
        `🔱 ${who} are **${w}-${g - w}** as a three. ${frame}`,
        `⛓️ The trio that keeps delivering: ${who}, **${w}-${g - w}** together. ${frame}`,
      ];
    } else {
      opts = [
        `🕳️ ${who} are **${w}-${g - w}** whenever all three line up. ${frame}`,
        `🥀 History is unkind to ${who} as a three — **${w}-${g - w}**. ${frame}`,
      ];
    }
    return choose(opts);
  }
  if (t === 'perfect') {
    [a, b] = [name(d['ids'][0]), name(d['ids'][1])];
    n = d['n'];
    if (d['won']) {
      opts = [
        `💯 ${a} & ${b} are unbeaten together these ${90} days — a flawless **${n}-0**. ${frame}`,
        `🏆 Perfect record on the line: ${a} & ${b} are **${n}-0** together. ${frame}`,
      ];
    } else {
      opts = [
        `🪦 The cursed duo returns: ${a} & ${b} are still winless together these ${90} days (0-${n}). ${frame}`,
        `💀 ${a} & ${b} are **0-${n}** as teammates — paired up again. ${frame}`,
      ];
    }
    return choose(opts);
  }
  if (t === 'mate_wr') {
    [p, q] = [name(d['p']), name(d['q'])];
    [wr, base, g] = [legacyRound(d['wr'] * 100), legacyRound(d['base'] * 100), d['games']];
    if (d['kind'] === 'worst') {
      if (d['wr'] === 0.0) {
        opts = [
          `🪦 ${p} hasn't won a single game with ${q} in the window (0-fer over ${g}). ${frame}`,
        ];
      } else {
        opts = [
          `🧨 ${p} sinks to **${wr}%** beside ${q} (vs **${base}%** overall, ${g}g) — paired again. ${frame}`,
          `💀 History says ${p} & ${q} don't click: **${wr}%** together vs **${base}%** apart. ${frame}`,
        ];
      }
    } else {
      if (d['wr'] === 1.0) {
        opts = [
          `🏆 ${p} & ${q} haven't lost as a duo in the window — ${p}'s ${base}% leaps to a perfect 100%. ${frame}`,
        ];
      } else {
        opts = [
          `🚀 ${p} is a different player next to ${q} — **${wr}%** together vs **${base}%** otherwise (${g}g). ${frame}`,
          `🍀 ${q} is ${p}'s lucky charm: **${wr}%** as a duo vs **${base}%** overall (${g}g). ${frame}`,
        ];
      }
    }
    return choose(opts);
  }
  if (t === 'h2h') {
    [winner, loser, k, series] = [name(d['winner']), name(d['loser']), d['k'], d['series']];
    if (d['sweep']) {
      opts = [
        `⚔️ ${winner} has beaten ${loser} **${k} straight** — ${loser} hasn't won this one in the window. ${frame}`,
        `😤 ${loser} is 0-for-the-last-${k} against ${winner}. ${frame}`,
      ];
    } else {
      opts = [
        `🔁 ${winner} owns this lately: **${k} in a row** over ${loser} (of ${series} meetings). ${frame}`,
        `🔒 ${k} straight to ${winner} over ${loser}. ${frame}`,
      ];
    }
    return choose(opts);
  }
  if (t === 'mate') {
    [a, b, k, series] = [name(d['ids'][0]), name(d['ids'][1]), d['k'], d['series']];
    if (d['won']) {
      opts = [
        `🔥 ${a} & ${b} are on fire — **${k} straight wins** together (of ${series}). ${frame}`,
        `📈 ${a} & ${b} just keep winning side-by-side (${k} in a row, ${series} in the window). ${frame}`,
      ];
    } else {
      opts = [
        `🪦 ${a} & ${b} have **lost their last ${k}** as teammates (of ${series}). ${frame}`,
        `❄️ ${series} games as a duo and ${a} & ${b} are ice-cold: **${k} straight losses**. ${frame}`,
      ];
    }
    return choose(opts);
  }
  if (t === 'deadlock') {
    [a, b, each, n] = [name(d['ids'][0]), name(d['ids'][1]), d['each'], d['n']];
    opts = [
      `⚖️ Deadlocked: ${a} & ${b} have split their last ${n} meetings **${each}-${each}**. ${frame}`,
      `🎯 The decider: ${a} vs ${b} is knotted **${each}-${each}** over their last ${n}. ${frame}`,
      `🪢 ${n} recent meetings, **${each}-${each}** — ${a} & ${b} can't be separated. ${frame}`,
    ];
    return choose(opts);
  }
  if (t === 'form') {
    [p, k] = [name(d['p']), d['k']];
    if (d['won']) {
      opts = [
        `🚀 ${p} rolls in on a **${k}-game win streak**. ${frame}`,
        `👑 **${k} straight wins** for ${p}. ${frame}`,
      ];
    } else {
      opts = [
        `🩹 ${p} is on a **${k}-game skid**. ${frame}`,
        `📉 Rough patch for ${p}: **${k} losses** in a row. ${frame}`,
      ];
    }
    return choose(opts);
  }
  throw Error('Unsupported storyline type');
}

export function payoffPhrase(
  c: StoryCandidate,
  nick: Record<string, string>,
  frame: string,
  choose: (xs: string[]) => string,
  came_true: boolean,
): string {
  const name = (uid: string) => `**${nick[uid] ?? 'someone'}**`;
  let d: StoryCandidate['data'],
    t: string,
    w: number,
    g: number,
    nw: number,
    ng: number,
    who: string,
    a: string,
    b: string,
    n: number,
    p: string,
    q: string,
    lo: string,
    k: number,
    each: number,
    ahead: string,
    behind: string;
  [d, t] = [c['data'], c['type']];
  if (t === 'lineup') {
    [w, g] = [d['wins'], d['games']];
    [nw, ng] = [w + (came_true ? 1 : 0), g + 1];
    if (came_true) {
      return choose([
        `🃏 The reunion delivered — this group is now **${nw}-${ng - nw}**.`,
        `🎰 Same side, same result. **${nw}-${ng - nw}** all told. ${frame}`,
      ]);
    }
    return choose([
      `🃏 The reunion fell flat. This group drops to **${nw}-${ng - nw}**.`,
      `🎰 Not this time — **${nw}-${ng - nw}** for this group. ${frame}`,
    ]);
  }
  if (t === 'trio') {
    who = joinNames(d['ids'].map((u: string) => name(u)));
    [w, g] = [d['wins'], d['games']];
    [nw, ng] = [w + (came_true ? 1 : 0), g + 1];
    if (came_true === d['won']) {
      return choose([
        `🔱 True to form: ${who} move to **${nw}-${ng - nw}** as a three.`,
        `⛓️ The trio held serve — **${nw}-${ng - nw}** together now. ${frame}`,
      ]);
    }
    return choose([
      `🔱 Against the grain: ${who} are now **${nw}-${ng - nw}** as a three.`,
      `🕳️ The pattern cracked — **${nw}-${ng - nw}** for the three of them. ${frame}`,
    ]);
  }
  if (t === 'perfect') {
    [a, b, n] = [name(d['ids'][0]), name(d['ids'][1]), d['n']];
    if (d['won']) {
      return came_true
        ? choose([
            `💯 Still flawless. ${a} & ${b} are **${n + 1}-0** together.`,
            `🏆 The perfect record survives — **${n + 1}-0**. ${frame}`,
          ])
        : choose([
            `💔 It ends here. ${a} & ${b} finally drop one as a pair — **${n}-1**.`,
            `🧨 The flawless run is over at ${n}. ${frame}`,
          ]);
    }
    return came_true
      ? choose([
          `🎉 THE ${90}-DAY CURSE BREAKS. ${a} & ${b} finally win together — **1-${n}**.`,
          `🔓 ${n} tries, and it lands. ${a} & ${b} are on the board. ${frame}`,
        ])
      : choose([
          `🪦 The curse holds. ${a} & ${b} fall to **0-${n + 1}**.`,
          `💀 Still winless together — **0-${n + 1}**. ${frame}`,
        ]);
  }
  if (t === 'mate_wr') {
    [p, q] = [name(d['p']), name(d['q'])];
    if (d['kind'] === 'best') {
      return came_true
        ? choose([
            `🚀 The pairing delivered again — ${p} keeps cashing in next to ${q}.`,
            `🍀 Lucky charm confirmed. ${p} & ${q} do it again. ${frame}`,
          ])
        : choose([
            `🌧️ Not tonight. Even alongside ${q}, ${p} came up short.`,
            `📉 The magic pairing missed this one. ${frame}`,
          ]);
    }
    return came_true
      ? choose([
          `🎯 History bucked — ${p} & ${q} finally made it work.`,
          `🔨 The bad pairing broke its own pattern. ${frame}`,
        ])
      : choose([
          `🔁 History repeats. ${p} & ${q} still do not click.`,
          `🧊 Same story as ever for ${p} beside ${q}. ${frame}`,
        ]);
  }
  if (t === 'h2h') {
    const [leader, lo, k] = [name(d['winner']), name(d['loser']), d['k']];
    if (came_true) {
      return choose([
        `⚔️ Make it **${k + 1} straight** — ${leader} still owns ${lo}.`,
        `🔒 ${lo} still has no answer. ${k + 1} in a row to ${leader}. ${frame}`,
      ]);
    }
    return choose([
      `🎊 The streak dies at ${k}. ${lo} finally beat ${leader}.`,
      `⛓️‍💥 ${lo} breaks the run. ${frame}`,
    ]);
  }
  if (t === 'mate') {
    [a, b, k] = [name(d['ids'][0]), name(d['ids'][1]), d['k']];
    if (d['won']) {
      return came_true
        ? choose([
            `🔥 **${k + 1} in a row** together for ${a} & ${b}.`,
            `📈 The duo streak lives — ${k + 1} straight. ${frame}`,
          ])
        : choose([
            `❄️ The run ends at ${k}. ${a} & ${b} finally drop one together.`,
            `🛑 Streak over. ${frame}`,
          ]);
    }
    return came_true
      ? choose([
          `🌅 The skid is over — ${a} & ${b} win one together at last.`,
          `🩹 ${k} straight losses, and then this. ${frame}`,
        ])
      : choose([
          `🪦 Make it **${k + 1} straight losses** for ${a} & ${b}.`,
          `❄️ Still ice-cold together — ${k + 1} in a row. ${frame}`,
        ]);
  }
  if (t === 'deadlock') {
    [a, b, each] = [name(d['ids'][0]), name(d['ids'][1]), d['each']];
    [ahead, behind] = came_true ? [a, b] : [b, a];
    return choose([
      `⚖️ Tie broken. ${ahead} edges ahead of ${behind}, **${each + 1}-${each}**.`,
      `🎯 Someone had to. ${ahead} takes the decider. ${frame}`,
    ]);
  }
  if (t === 'form') {
    [p, k] = [name(d['p']), d['k']];
    if (d['won']) {
      return came_true
        ? choose([
            `🚀 **${k + 1} straight** for ${p}. Nobody has stopped them yet.`,
            `👑 The heater rolls on — ${k + 1} in a row. ${frame}`,
          ])
        : choose([
            `🛑 The run ends at ${k}. ${p} finally drops one.`,
            `📉 Streak over for ${p}. ${frame}`,
          ]);
    }
    return came_true
      ? choose([
          `🌅 The slump breaks. ${p} snaps a ${k}-game skid.`,
          `🎊 ${k} losses, then this. ${frame}`,
        ])
      : choose([
          `🩹 Make it **${k + 1}**. The skid goes on for ${p}.`,
          `📉 Still searching — ${k + 1} straight for ${p}. ${frame}`,
        ]);
  }
  throw Error('Unsupported storyline type');
}

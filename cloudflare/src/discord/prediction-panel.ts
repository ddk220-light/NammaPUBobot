import type { Match } from '../domain/play';
import { stakeOptions, type Bet, type Book } from '../domain/economy';
import type { ActionRow } from './cards';

export function predictionPanel(
  m: Match,
  book: Book | undefined,
  user: string,
  balance: number,
  side?: 'alpha' | 'beta',
  bettingEnabled = true,
  currentBet?: Bet | null,
): { content: string; components: ActionRow[] } {
  if (!book || book.status !== 'open' || !['playing', 'check_in'].includes(m.state))
    return { content: 'Betting is closed.', components: [] };
  const own = m.teams.findIndex((t) => t.some((p) => p.id === user));
  const selected = own >= 0 ? (own === 0 ? 'alpha' : 'beta') : (currentBet?.side ?? side);
  if (!selected && bettingEnabled)
    return {
      content: `You hold ${balance} gold. Who will win?`,
      components: [
        {
          type: 1,
          components: ['alpha', 'beta'].map((side) => ({
            type: 2 as const,
            style: 2,
            label: side === 'alpha' ? 'Back Alpha' : 'Back Beta',
            custom_id: `bp:${book.id}:${side === 'alpha' ? 'a' : 'b'}:${user}`,
          })),
        },
      ],
    };
  const options = bettingEnabled ? stakeOptions(balance) : [];
  return {
    content: bettingEnabled
      ? `Back ${selected === 'alpha' ? 'Alpha' : 'Beta'} · you hold ${balance} gold. ${currentBet ? `Your bet: ${currentBet.stake} gold. ` : ''}${options.length ? 'Choose your stake below.' : 'You need at least 10 gold to bet.'}`
      : 'New bets are disabled. You can still cancel your existing bet.',
    components: [
      {
        type: 1,
        components: [
          ...options.map((stake) => ({
            type: 2 as const,
            style: 3,
            label: `${stake} gold`,
            custom_id: `bs:${book.id}:${selected === 'alpha' ? 'a' : 'b'}:${user}:${stake}`,
          })),
          {
            type: 2,
            style: 2,
            label: 'Cancel bet',
            custom_id: `bc:${book.id}:${user}`,
            ...(currentBet === null ? { disabled: true } : {}),
          },
        ],
      },
    ],
  };
}

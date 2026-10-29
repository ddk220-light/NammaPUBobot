import type { Card } from './cards';
import { publicGameListText, type PublicGameList } from '../games/discovery';
import type { ProviderResult } from '../games/types';

const safe = (s: unknown) =>
  String(s ?? '')
    .replace(/[@`*_~<>\\\[\]]/g, ' ')
    .replace(/[\r\n]/g, ' ');

/** Ten bounded cards fit Discord's combined 6,000-character embed budget. */
export function gameListReply(
  result: ProviderResult<PublicGameList>,
): Pick<Card, 'content' | 'embeds'> {
  if (result.status !== 'ok' || !result.value.games.length)
    return { content: publicGameListText(result), embeds: [] };
  return {
    content: `${result.value.phase === 'running' ? 'Running games' : 'Waiting lobbies'} · bounded public snapshot, checked <t:${Math.floor(result.value.checkedAt / 1000)}:T>.`,
    embeds: result.value.games.slice(0, 10).map((g, i) => {
      const players = g.players
        .map(
          (p) =>
            `${safe(p.name).slice(0, 24)} — ${safe(p.selection ?? 'faction unknown').slice(0, 28)}`,
        )
        .join('\n');
      const header = `${safe(g.map ?? 'Map unknown').slice(0, 50)} · ${g.elapsedDuration ? `${Math.floor(g.elapsedDuration.value / 60)} min` : 'duration unknown'}\n`;
      const shortened =
        players.length > 330
          ? `${players.slice(0, 290)}\nFull roster available in organizer.`
          : players;
      return {
        title: `${i + 1}. ${safe(g.externalId)}`.slice(0, 75),
        description: (header + shortened).slice(0, 430),
        color: 0x5865f2,
        footer: {
          text: `${safe(g.provider ?? g.source)} · ${new Date(g.observedAt).toISOString()}`.slice(
            0,
            65,
          ),
        },
      };
    }),
  };
}

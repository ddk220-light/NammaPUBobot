import type { Settings } from '../src/domain/config';

/** Stored ladders can outlive their queues, including rehearsal/test ladders. */
export function playerGames(settings: Settings, includeWithoutQueues = false) {
  const configured = new Set(settings.queues.map((queue) => queue.gameId));
  return settings.games.filter((game) => includeWithoutQueues || configured.has(game.id));
}

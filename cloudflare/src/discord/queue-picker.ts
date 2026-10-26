import type { Queue } from '../domain/config';
import type { ActionRow } from './cards';

export type QueueChoice = 'add' | 'remove' | 'queue';
export function queuePicker(queues: Queue[], kind: QueueChoice, page = 0) {
  const last = Math.max(0, Math.ceil(queues.length / 25) - 1);
  page = Math.max(0, Math.min(page, last));
  const components: ActionRow[] = [
    {
      type: 1,
      components: [
        {
          type: 3,
          custom_id: `qs:${kind}:${page}`,
          placeholder: 'Choose a queue',
          min_values: 1,
          max_values: 1,
          options: queues.slice(page * 25, (page + 1) * 25).map((q) => ({
            label: q.name,
            value: q.id,
            description: q.enabled ? `${q.size} players` : 'Paused',
          })),
        },
      ],
    },
  ];
  if (last)
    components.push({
      type: 1,
      components: [
        {
          type: 2,
          style: 2,
          label: 'Previous',
          custom_id: `qp:${kind}:${Math.max(0, page - 1)}`,
          disabled: page === 0,
        },
        {
          type: 2,
          style: 2,
          label: 'Next',
          custom_id: `qp:${kind}:${Math.min(last, page + 1)}`,
          disabled: page === last,
        },
      ],
    });
  return {
    content: queues.length ? 'Choose a queue.' : 'No queues are configured here yet.',
    components: queues.length ? components : [],
  };
}

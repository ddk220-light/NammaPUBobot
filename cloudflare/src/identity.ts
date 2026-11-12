const repository = 'https://github.com/ddk220-light/NammaPUBobot';

export const identity = {
  internalName: 'NammaMatchBot',
  publicName: 'RTS Pickup',
  repository,
  license: `${repository}/blob/main/LICENSE`,
  mission:
    'Make community game nights easier to organize with transparent tools for queues, teams, ratings and match history.',
  // The owner supplies these before launch. Pages show only the routes that are set, and
  // scripts/check-site.mjs refuses an indexed build while operator, email or support server is null.
  /** The person or group that runs the hosted service, as it should appear publicly. */
  operator: null as string | null,
  contact: {
    /** A monitored address for private requests such as data deletion. */
    email: null as string | null,
    /** A Discord invite URL for the support server, e.g. https://discord.gg/… */
    supportServer: null as string | null,
    /** Public bug reports and questions. */
    issues: `${repository}/issues`,
  },
};

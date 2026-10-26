export type VoiceMember = { userId: string; channelId: string };
/** Snapshot completeness and transport health are separate from last occupant movement. */
export class PresenceSnapshot {
  guilds = new Map<string, Map<string, string>>();
  healthy = false;
  initialize(guild: string, states: { user_id: string; channel_id: string | null }[]) {
    this.guilds.set(
      guild,
      new Map(states.filter((s) => s.channel_id).map((s) => [s.user_id, s.channel_id!])),
    );
  }
  update(guild: string, user: string, channel: string | null) {
    const members = this.guilds.get(guild);
    if (!members) return;
    if (channel) members.set(user, channel);
    else members.delete(user);
  }
  members(guild: string): VoiceMember[] | null {
    const members = this.guilds.get(guild);
    return this.healthy && members
      ? [...members].map(([userId, channelId]) => ({ userId, channelId }))
      : null;
  }
  invalidate() {
    this.healthy = false;
  }
}

export type VoiceMember = {
  userId: string;
  channelId: string;
  joinedAt: number | null;
  bot?: boolean;
};
type Member = { user?: { id?: string; bot?: boolean } };
type Channel = {
  id: string;
  type: number;
  permission_overwrites?: { id: string; allow: string; deny: string }[];
};
type Role = { id: string; permissions: string };
const bot = (member: Member | undefined, id: string) =>
  member?.user?.id === id ? member.user.bot === true : undefined;
const viewChannel = 1024n;
function permissions(value?: string) {
  return value && /^\d+$/.test(value) ? BigInt(value) : 0n;
}
/** Snapshot completeness and transport health are separate from last occupant movement. */
export class PresenceSnapshot {
  guilds = new Map<string, Map<string, VoiceMember>>();
  private channels = new Map<string, Map<string, Channel>>();
  private everyone = new Map<string, string>();
  healthy = false;
  initialize(
    guild: string,
    states: { user_id: string; channel_id: string | null; member?: Member }[],
    metadata?: { members?: Member[]; channels?: Channel[]; roles?: Role[] },
  ) {
    const members = new Map(metadata?.members?.map((m) => [m.user?.id, m]));
    this.guilds.set(
      guild,
      new Map(
        states
          .filter((s) => s.channel_id)
          .map((s) => [
            s.user_id,
            {
              userId: s.user_id,
              channelId: s.channel_id!,
              joinedAt: null,
              bot: bot(s.member, s.user_id) ?? bot(members.get(s.user_id), s.user_id),
            },
          ]),
      ),
    );
    this.channels.set(guild, new Map(metadata?.channels?.map((c) => [c.id, c])));
    this.everyone.set(guild, metadata?.roles?.find((r) => r.id === guild)?.permissions ?? '0');
  }
  update(guild: string, user: string, channel: string | null, member?: Member, now = Date.now()) {
    const members = this.guilds.get(guild);
    if (!members) return;
    const previous = members.get(user);
    if (channel)
      members.set(user, {
        userId: user,
        channelId: channel,
        joinedAt: previous?.channelId === channel ? previous.joinedAt : now,
        bot: bot(member, user) ?? previous?.bot,
      });
    else members.delete(user);
  }
  channel(guild: string, channel: Channel, deleted = false) {
    if (deleted) {
      this.channels.get(guild)?.delete(channel.id);
      for (const [id, m] of this.guilds.get(guild) ?? [])
        if (m.channelId === channel.id) this.guilds.get(guild)!.delete(id);
    } else this.channels.get(guild)?.set(channel.id, channel);
  }
  role(guild: string, role: Role) {
    if (role.id === guild) this.everyone.set(guild, role.permissions);
  }
  remove(guild: string) {
    this.guilds.delete(guild);
    this.channels.delete(guild);
    this.everyone.delete(guild);
  }
  members(guild: string): VoiceMember[] | null {
    const members = this.guilds.get(guild);
    return this.healthy && members ? [...members.values()] : null;
  }
  visibleMembers(guild: string): VoiceMember[] | null {
    const members = this.members(guild);
    if (!members) return null;
    return members
      .filter((m) => {
        if (m.bot !== false) return false; // Unknown identities are not assumed to be human.
        const channel = this.channels.get(guild)?.get(m.channelId);
        if (!channel || ![2, 13].includes(channel.type)) return false;
        const overwrites = channel.permission_overwrites ?? [];
        // Do not expose occupants of restricted channels to a broader text audience.
        if (overwrites.some((o) => (permissions(o.deny) & viewChannel) !== 0n)) return false;
        const everyone = overwrites.find((o) => o.id === guild);
        return (
          ((permissions(this.everyone.get(guild)) | permissions(everyone?.allow)) & viewChannel) !==
          0n
        );
      })
      .sort((a, b) => (b.joinedAt ?? 0) - (a.joinedAt ?? 0) || a.userId.localeCompare(b.userId));
  }
  invalidate() {
    this.healthy = false;
  }
}

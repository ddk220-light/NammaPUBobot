// Rank synchronization may grant cosmetic roles only. Recheck at delivery time,
// because a server administrator can change a saved role's permissions later.
// Discord's permission flags: moderation, management and private server analytics.
// https://docs.discord.com/developers/topics/permissions
export const privilegedRankPermissions = [
  1, 2, 3, 4, 5, 7, 13, 19, 22, 23, 24, 27, 28, 29, 30, 33, 34, 40, 41, 51, 52,
].reduce((mask, bit) => mask | (1n << BigInt(bit)), 0n);

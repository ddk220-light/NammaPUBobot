__all__ = [
	'noadds', 'noadd', 'forgive', 'rating_seed', 'rating_penality', 'rating_hide',
	'rating_reset', 'rating_snap', 'stats_reset', 'stats_reset_player', 'stats_replace_player',
	'phrases_add', 'phrases_clear', 'undo_match', 'identity_link', 'identity_show', 'identity_conflicts',
	'douche_add', 'douche_summary', 'douche_leaderboard'
]

from time import time
from datetime import timedelta
from nextcord import Member, Embed, Colour

from core.console import log
from core.utils import seconds_to_str, get_nick

import bot


async def noadds(ctx):
	data = await bot.noadds.get_noadds(ctx)
	now = int(time())
	s = "```markdown\n"
	s += ctx.qc.gt(" ID | Prisoner | Left | Reason")
	s += "\n----------------------------------------\n"
	if len(data):
		s += "\n".join((
			f" {i['id']} | {i['name']} | {seconds_to_str(max(0, (i['at'] + i['duration']) - now))} | {i['reason'] or '-'}"
			for i in data
		))
	else:
		s += ctx.qc.gt("Noadds are empty.")
	await ctx.reply(s + "\n```")


async def noadd(ctx, player: Member, duration: timedelta, reason: str = None):
	ctx.check_perms(ctx.Perms.MODERATOR)
	if not duration:
		duration = timedelta(hours=2)
	if duration > timedelta(days=365*100):
		raise bot.Exc.ValueError(ctx.qc.gt("Specified duration time is too long."))
	await bot.noadds.noadd(
		ctx=ctx, member=player, duration=int(duration.total_seconds()), moderator=ctx.author, reason=reason
	)
	await ctx.success(ctx.qc.gt("Banned **{member}** for `{duration}`.").format(
		member=get_nick(player),
		duration=duration.__str__()
	))


async def forgive(ctx, player: Member):
	ctx.check_perms(ctx.Perms.MODERATOR)
	if await bot.noadds.forgive(ctx=ctx, member=player, moderator=ctx.author):
		await ctx.success(ctx.qc.gt("Done."))
	else:
		raise bot.Exc.NotFoundError(ctx.qc.gt("Specified member is not banned."))


async def rating_seed(ctx, player: str, rating: int, deviation: int = None):
	ctx.check_perms(ctx.Perms.MODERATOR)
	if (player := await ctx.get_member(player)) is None:
		raise bot.Exc.SyntaxError(f"Specified member not found on the server.")  # noqa: F541
	if not 0 < rating < 10000 or not 0 < (deviation or 1) < 3000:
		raise bot.Exc.ValueError("Bad rating or deviation value.")

	await ctx.qc.rating.set_rating(player, rating=rating, deviation=deviation, reason="manual seeding")
	await ctx.qc.update_rating_roles(player)
	await ctx.success(ctx.qc.gt("Done."))


async def rating_penality(ctx, player: str, penality: int, reason: str = None):
	ctx.check_perms(ctx.Perms.MODERATOR)
	if (player := await ctx.get_member(player)) is None:
		raise bot.Exc.SyntaxError(f"Specified member not found on the server.")  # noqa: F541
	if abs(penality) > 10000:
		raise ValueError("Bad penality value.")
	reason = "penality: " + reason if reason else "penality by a moderator"

	await ctx.qc.rating.set_rating(player, penality=penality, reason=reason)
	await ctx.qc.update_rating_roles(player)
	await ctx.success(ctx.qc.gt("Done."))


async def rating_hide(ctx, player: str, hide: bool = True):
	ctx.check_perms(ctx.Perms.MODERATOR)
	if (player := await ctx.get_member(player)) is None:
		raise bot.Exc.SyntaxError(f"Specified member not found on the server.")  # noqa: F541
	await ctx.qc.rating.hide_player(player.id, hide=hide)
	await ctx.success(ctx.qc.gt("Done."))


async def rating_reset(ctx):
	ctx.check_perms(ctx.Perms.ADMIN)
	await ctx.qc.rating.reset()
	await ctx.success(ctx.qc.gt("Done."))


async def rating_snap(ctx):
	ctx.check_perms(ctx.Perms.ADMIN)
	await ctx.qc.rating.snap_ratings(ctx.qc._ranks_table)
	await ctx.success(ctx.qc.gt("Done."))


async def stats_reset(ctx):
	ctx.check_perms(ctx.Perms.ADMIN)
	await bot.stats.reset_channel(ctx.qc.id)
	await ctx.success(ctx.qc.gt("Done."))


async def stats_reset_player(ctx, player: str):
	ctx.check_perms(ctx.Perms.MODERATOR)
	if (player := await ctx.get_member(player)) is None:
		raise bot.Exc.SyntaxError(f"Specified member not found on the server.")  # noqa: F541

	await bot.stats.reset_player(ctx.qc.id, player.id)
	await ctx.success(ctx.qc.gt("Done."))


async def stats_replace_player(ctx, player1: str, player2: str):
	ctx.check_perms(ctx.Perms.ADMIN)
	if (player1 := await ctx.get_member(player1)) is None:
		raise bot.Exc.SyntaxError(f"Specified member not found on the server.")  # noqa: F541
	if (player2 := await ctx.get_member(player2)) is None:
		raise bot.Exc.SyntaxError(f"Specified member not found on the server.")  # noqa: F541

	await bot.stats.replace_player(ctx.qc.id, player1.id, player2.id, get_nick(player2))
	await ctx.success(ctx.qc.gt("Done."))


async def phrases_add(ctx, player: Member, phrase: str):
	ctx.check_perms(ctx.Perms.MODERATOR)
	await bot.noadds.phrases_add(ctx, player, phrase)
	await ctx.success(ctx.qc.gt("Done."))


async def phrases_clear(ctx, player: Member):
	ctx.check_perms(ctx.Perms.MODERATOR)
	await bot.noadds.phrases_clear(ctx, member=player)
	await ctx.success(ctx.qc.gt("Done."))


async def undo_match(ctx, match_id: int):
	ctx.check_perms(ctx.Perms.MODERATOR)

	result = await bot.stats.undo_match(ctx, match_id)
	if result:
		await ctx.success(ctx.qc.gt("Done."))
	else:
		raise bot.Exc.NotFoundError(ctx.qc.gt("Could not find match with specified id."))


async def identity_link(ctx, member: Member, profile_id: int, force: bool = False):
	""" Manual override for bot/identity.py's resolver: records `profile_id`
	as belonging to `member` with confidence='manual', which no automated
	learning (seed/replay ingest) can ever overwrite -- see identity.learn's
	docstring. If `profile_id` already resolves to a *different* Discord
	user, this refuses to reassign it unless `force` is set, so a mistyped
	profile id can't silently steal someone else's identity.

	`force` goes through identity.relink(), NOT identity.learn(): learn()
	refuses an equal-tier write to a different user (identity v2's tie rule),
	so forcing through it would refuse the reassignment, record an `open`
	conflict, and still reply "Linked" -- a false success. relink() is the one
	writer allowed to move a `manual` binding, and it is what `force` has
	always claimed to do.

	Note what relink() does beyond moving this one profile: it also releases
	every OTHER profile `member` owns (spec section 3 -- a member who owns two
	profiles has their statistics double-attributed). Each released profile is
	recorded in identity_conflicts as `superseded`. The `additional: true`
	flag that opts out of that, for genuine multi-account players, lands with
	the identity v2 command wiring; until then a forced link is a full
	relink. """
	ctx.check_perms(ctx.Perms.ADMIN)

	existing_owner = await bot.identity.user_for_profile(profile_id)
	if existing_owner is not None and existing_owner != member.id:
		if not force:
			raise bot.Exc.ValueError(ctx.qc.gt(
				"Profile `{profile_id}` is already linked to <@{owner_id}>. "
				"Re-run with `force: True` to relink it to **{member}**."
			).format(profile_id=profile_id, owner_id=existing_owner, member=get_nick(member)))
		await bot.identity.relink(profile_id, member.id)
	else:
		await bot.identity.learn(profile_id, member.id, source="manual")

	await ctx.success(ctx.qc.gt("Linked profile `{profile_id}` to **{member}**.").format(
		profile_id=profile_id, member=get_nick(member)
	))

	# An admin correction is a new constraint for the deduction solver (spec
	# section 4): this member is now ruled out as a candidate for every other
	# profile in the games they played, which can immediately resolve a
	# teammate. Deliberately AFTER the reply, and guarded twice -- run_for_channel
	# already swallows its own failures and skips an unenrolled channel quietly,
	# but the link has LANDED by this point, so nothing that happens here may
	# ever reach the admin as a failed command.
	try:
		from bot import identity_solver

		await identity_solver.run_for_channel(ctx.qc.id)
	except Exception as e:
		log.error(f"identity solver trigger failed after an admin link of profile {profile_id}: {e}")


async def identity_show(ctx, member: Member):
	""" Read-only lookup: `member`'s known AoE2 profile ids, from
	bot/identity.py's global profile_id<->user_id map.

	It used to print a per-community nick alongside them, from
	identity_aliases -- that table was write-never and is deleted in identity
	v2, since Discord supplies display names live. The community lookup that
	fed it went with it. """
	ctx.check_perms(ctx.Perms.MODERATOR)

	profiles = await bot.identity.profiles_for_users([member.id])
	profile_ids = profiles.get(member.id, [])

	embed = Embed(title=ctx.qc.gt("Identity — {member}").format(member=get_nick(member)), colour=Colour(0x5865F2))
	embed.add_field(
		name=ctx.qc.gt("AoE2 profiles"),
		value=", ".join(f"`{pid}`" for pid in profile_ids) if profile_ids else ctx.qc.gt("(none known)"),
		inline=False
	)
	await ctx.reply(embed=embed)


async def identity_conflicts(ctx):
	""" Read-only lookup: every open profile_id<->user_id disagreement that
	learn() or migration 003_seed_identities recorded instead of silently
	discarding a losing claim (see bot/identity.py's identity_conflicts
	declaration and open_conflicts()). There is no resolution UI yet -- this
	just surfaces what open_conflicts() already tracks so a moderator isn't
	blind to it in the meantime; nothing here changes `status`. """
	ctx.check_perms(ctx.Perms.MODERATOR)

	conflicts = await bot.identity.open_conflicts()
	if not conflicts:
		await ctx.reply(ctx.qc.gt("no open identity conflicts"))
		return

	embed = Embed(title=ctx.qc.gt("Open identity conflicts"), colour=Colour(0x5865F2))
	for c in conflicts:
		owner = f"<@{c['current_owner']}>" if c["current_owner"] is not None else ctx.qc.gt("(none known)")
		claimants = "\n".join(f"<@{claim['user_id']}> ({claim['source']})" for claim in c["claims"])
		embed.add_field(
			name=ctx.qc.gt("Profile `{profile_id}`").format(profile_id=c["profile_id"]),
			value=f"{ctx.qc.gt('Current owner')}: {owner}\n{ctx.qc.gt('Competing claim(s)')}:\n{claimants}",
			inline=False,
		)
	await ctx.reply(embed=embed)


async def douche_add(ctx, player: Member, target: Member):
	ctx.check_perms(ctx.Perms.MODERATOR)
	if (member := await ctx.get_member(player)) is None:
		raise bot.Exc.NotFoundError(ctx.qc.gt("Specified user not found."))
	if (target_member := await ctx.get_member(target)) is None:
		raise bot.Exc.NotFoundError(ctx.qc.gt("Specified user not found."))
	await bot.douche.douche.add(ctx.channel.guild.id, member, target_member, ctx.author)
	await ctx.success(ctx.qc.gt("Recorded: **{member}** douched **{target}**.").format(
		member=get_nick(member), target=get_nick(target_member)
	))


async def douche_summary(ctx, player: Member = None):
	target = ctx.author if player is None else await ctx.get_member(player)
	if not target:
		raise bot.Exc.NotFoundError(ctx.qc.gt("Specified user not found."))
	data = await bot.douche.douche.user_summary(ctx.channel.guild.id, target)
	embed = Embed(title=f"Douche record — {get_nick(target)}", colour=Colour(0xCD5C5C))
	embed.add_field(name="Received", value=str(data['received']), inline=True)
	embed.add_field(name="Given", value=str(data['given']), inline=True)
	if data['recent']:
		now = int(time())
		embed.add_field(
			name="Recently douched",
			value="\n".join(
				f"• {r['target_name']} ({seconds_to_str(max(0, now - r['at']))} ago)"
				for r in data['recent']
			),
			inline=False
		)
	await ctx.reply(embed=embed)


async def douche_leaderboard(ctx):
	rows = await bot.douche.douche.leaderboard(ctx.channel.guild.id)
	if not rows:
		raise bot.Exc.NotFoundError(ctx.qc.gt("No douche records yet."))
	embed = Embed(title="Douche leaderboard", colour=Colour(0xCD5C5C))
	embed.add_field(
		name="Player",
		value="\n".join(f"**{i + 1}.** {r['name']}" for i, r in enumerate(rows)),
		inline=True
	)
	embed.add_field(
		name="Count",
		value="\n".join(str(r['count']) for r in rows),
		inline=True
	)
	await ctx.reply(embed=embed)

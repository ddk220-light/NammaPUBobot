__all__ = [
	'noadds', 'noadd', 'forgive', 'rating_seed', 'rating_penality', 'rating_hide',
	'rating_reset', 'rating_snap', 'stats_reset', 'stats_reset_player', 'stats_replace_player',
	'phrases_add', 'phrases_clear', 'undo_match', 'identity_link', 'identity_unlink', 'identity_show',
	'identity_status', 'identity_conflicts',
	'douche_add', 'douche_summary', 'douche_leaderboard'
]

from time import time
from datetime import timedelta
from nextcord import Member, Embed, Colour
from nextcord.utils import escape_markdown

from core.console import log
from core.utils import seconds_to_str, get_nick

import bot

# What each of identity.CONFIDENCE_ORDER's tiers MEANS to the human reading
# `/identity show`. The bare lattice value is internal jargon, and the one
# question an admin is asking of it -- did a person decide this, or did a
# program guess it? -- is exactly what the raw word does not answer.
CONFIDENCE_GLOSS = {
	"seed": "seeded from the old mapping",
	"learned": "deduced automatically",
	"self": "linked by the player",
	"manual": "set by an admin",
}


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


async def identity_link(ctx, member: Member, profile_id: int, additional: bool = False):
	""" The admin correction: bind `profile_id` to `member` at confidence
	`manual`, the tier no automated writer (seed, replay ingest, the deduction
	solver) can ever overwrite -- see identity.learn's docstring.

	Two shapes, and `additional` is the whole difference:

	  additional=False (default) -- `member` ends up owning EXACTLY this
	    profile. Every other profile they own is released in the same call.
	    This is "the link is wrong, here is the right one": a member owning two
	    profiles has their statistics double-attributed, since every consumer
	    resolves profile -> user through `identities`.
	  additional=True -- this profile is ADDED to whatever they already own.
	    Multi-account players are real (5 in the flagship data, up to 3
	    profiles each) and before this flag there was no command that could
	    give somebody a second account: the only reassignment path released
	    their others.

	Both go through identity.relink() rather than identity.learn(), and both
	work on a profile that currently belongs to somebody ELSE -- that is what
	an admin correction IS, and it needs no confirmation flag. learn() would
	refuse it (equal tier, different user, identity v2's tie rule), file an
	`open` conflict against the admin's own instruction, and still reply
	"Linked" -- a false success. relink() is the one writer allowed to move a
	`manual` binding; the displaced owner and every released profile are
	recorded in identity_conflicts as `superseded` (spec section 3).

	The reply names what was actually taken away, because a release is
	destructive and otherwise invisible: an admin who meant `additional: true`
	needs the ids back to undo it. """
	ctx.check_perms(ctx.Perms.ADMIN)
	# relink() coerces this too, but the release list below is compared against
	# ids read out of the DB, which are ints.
	profile_id = int(profile_id)

	# Both reads happen BEFORE the write -- afterwards the previous owner is
	# gone and the released profiles are no longer the member's.
	previous_owner = await bot.identity.user_for_profile(profile_id)
	owned = await bot.identity.profiles_for_users([member.id])
	others = sorted(pid for pid in owned.get(member.id, []) if pid != profile_id)

	await bot.identity.relink(profile_id, member.id, additional=additional)

	lines = [ctx.qc.gt(
		"Linked profile `{profile_id}` to **{member}** as an additional account."
		if additional else
		"Linked profile `{profile_id}` to **{member}**."
	).format(profile_id=profile_id, member=get_nick(member))]

	if previous_owner is not None and previous_owner != member.id:
		lines.append(ctx.qc.gt(
			"It was linked to <@{owner_id}> — that link is now superseded."
		).format(owner_id=previous_owner))

	if others:
		id_list = ", ".join(f"`{pid}`" for pid in others)
		lines.append(ctx.qc.gt(
			"They keep their other profile(s): {id_list}."
			if additional else
			"Released their other profile(s): {id_list} — those are now unowned. "
			"Re-link any of them with `additional: True` if that was not intended."
		).format(id_list=id_list))

	await ctx.success("\n".join(lines))

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


async def identity_unlink(ctx, member: Member, profile_id: int):
	""" Remove one binding with no replacement: the profile goes back to the
	unowned state (user_id NULL, confidence `seed`) and the removed claim is
	recorded as `unlinked`. Rarely needed now that `/identity link` is an
	atomic relink -- this is for "this link is simply wrong and I have nothing
	to put in its place".

	`member` is not decoration: it is the check. Unlinking by profile id alone
	would let one mistyped digit silently remove a third party's link, and
	nothing in the resulting reply would say so. The command refuses unless the
	profile really is that member's -- including when it is nobody's, since
	replying "unlinked" for a no-op would tell an admin their typo took
	effect.

	Deliberately does NOT trigger the deduction solver, unlike the link path. A
	link ADDS a constraint that can resolve a teammate; an unlink removes one,
	and running the solver against the profile just released would let it deduce
	a `learned` binding straight back onto the row an admin explicitly cleared.
	The next ordinary trigger (a replay ingest, somebody's `/link`) may still do
	that -- an unlink says "this is not established", not "never deduce this" --
	but it must not be this command that does it. """
	ctx.check_perms(ctx.Perms.ADMIN)
	profile_id = int(profile_id)

	owner = await bot.identity.user_for_profile(profile_id)
	if owner is None:
		raise bot.Exc.ValueError(ctx.qc.gt(
			"Profile `{profile_id}` isn't linked to anyone, so there's nothing to unlink."
		).format(profile_id=profile_id))
	if owner != member.id:
		raise bot.Exc.ValueError(ctx.qc.gt(
			"Profile `{profile_id}` is linked to <@{owner_id}>, not to **{member}**. "
			"Nothing was changed — re-run with the right member if you meant to remove that link."
		).format(profile_id=profile_id, owner_id=owner, member=get_nick(member)))

	await bot.identity.unlink(profile_id)

	await ctx.success(ctx.qc.gt(
		"Unlinked profile `{profile_id}` from **{member}**. It's unowned now, so anyone "
		"can claim it with `/link` — including **{member}** again."
	).format(profile_id=profile_id, member=get_nick(member)))


async def identity_show(ctx, member: Member):
	""" Read-only lookup: `member`'s known AoE2 profiles, from bot/identity.py's
	global profile_id<->user_id map.

	Each profile is shown with the in-game name last observed for it and the
	confidence tier that bound it. The bare ids alone cannot be checked by a
	human: the name is what an admin recognises the account by, and the tier is
	what separates an automated deduction from somebody's deliberate decision --
	the difference between "fix this" and "leave it alone".

	It used to print a per-community nick instead, from identity_aliases -- that
	table was write-never and is deleted in identity v2, since Discord supplies
	display names live. The community lookup that fed it went with it. """
	ctx.check_perms(ctx.Perms.MODERATOR)

	profiles = await bot.identity.profiles_for_users([member.id])
	profile_ids = sorted(profiles.get(member.id, []))
	names = await bot.identity.names_for_profiles(profile_ids)
	tiers = await bot.identity.confidence_for_profiles(profile_ids)

	lines = []
	for pid in profile_ids:
		line = f"`{pid}`"
		if name := names.get(pid):
			line += f" — {escape_markdown(name)}"
		if tier := tiers.get(pid):
			gloss = CONFIDENCE_GLOSS.get(tier)
			line += f" · {tier}" + (f" ({ctx.qc.gt(gloss)})" if gloss else "")
		lines.append(line)

	embed = Embed(title=ctx.qc.gt("Identity — {member}").format(member=get_nick(member)), colour=Colour(0x5865F2))
	embed.add_field(
		name=ctx.qc.gt("AoE2 profiles"),
		value="\n".join(lines) if lines else ctx.qc.gt("(none known)"),
		inline=False
	)
	await ctx.reply(embed=embed)


async def identity_status(ctx):
	""" How much of this community is actually linked — spec section 3's
	visibility surface.

	Every analysis feature resolves a player through `identities`, and an
	unlinked player is silently missing from all of them: no error, no warning,
	just a thinner number nobody can tell is thin. This command replaces that
	silence with a figure an admin can act on, plus the two things they can do
	about it (tell players to run `/link`, or resolve a contested profile).

	A channel that was never enrolled in a community has nothing to measure.
	That is the ordinary state for most channels, so it is answered plainly
	rather than raised as an error. """
	ctx.check_perms(ctx.Perms.MODERATOR)

	community_id = await bot.community.community_for_channel(ctx.channel.id)
	if community_id is None:
		await ctx.reply(ctx.qc.gt(
			"This channel isn't enrolled in a community, so there's no identity coverage to "
			"report for it. Enrollment happens by itself on a channel the bot is enabled on."
		))
		return

	days = bot.identity.COVERAGE_WINDOW_DAYS
	coverage = await bot.identity.coverage_for_community(community_id)

	embed = Embed(title=ctx.qc.gt("Identity coverage"), colour=Colour(0x5865F2))
	if coverage["players"] == 0:
		embed.add_field(
			name=ctx.qc.gt("No recent matches"),
			value=ctx.qc.gt(
				"Nobody has played a reported match here in the last {days} days, so there's "
				"nothing to measure yet."
			).format(days=days),
			inline=False
		)
	else:
		embed.add_field(
			name=ctx.qc.gt("Linked players"),
			value=ctx.qc.gt(
				"**{linked} of {players}** players seen in the last {days} days are linked "
				"to an AoE2 profile."
			).format(linked=coverage["linked"], players=coverage["players"], days=days),
			inline=False
		)
		if coverage["unlinked"]:
			embed.add_field(
				name=ctx.qc.gt("Not linked yet"),
				value=ctx.qc.gt(
					"**{unlinked}** aren't, so their games are missing from every stat that "
					"resolves a player. They can fix that themselves with `/link`, or you can "
					"with `/identity link`."
				).format(unlinked=coverage["unlinked"]),
				inline=False
			)
		else:
			embed.add_field(
				name=ctx.qc.gt("Not linked yet"),
				value=ctx.qc.gt("Nobody — every player seen in the window is linked."),
				inline=False
			)

	# Omitted entirely at zero: "0 open conflicts" is noise on the one surface
	# that exists to make a real number stand out.
	if coverage["conflicts"]:
		embed.add_field(
			name=ctx.qc.gt("Open conflicts"),
			value=ctx.qc.gt(
				"**{conflicts}** profile(s) have competing claims nobody has settled. "
				"Run `/identity conflicts` to see them."
			).format(conflicts=coverage["conflicts"]),
			inline=False
		)

	await ctx.reply(embed=embed)


async def identity_conflicts(ctx):
	""" Read-only lookup: every open profile_id<->user_id claim that was not
	applied -- recorded instead of silently discarded by learn()/link_self()
	losing a lattice comparison, by migration 003_seed_identities, or by
	bot/identity_solver.py refusing to auto-apply its own conclusion (an unstable
	one, or one that would hand a member a second profile; those two show with no
	current owner, and `identity link ... additional: True` is how a genuine
	second account is granted). See bot/identity.py's identity_conflicts
	declaration and open_conflicts(). There is no resolution UI yet -- this just
	surfaces what open_conflicts() already tracks so a moderator isn't blind to
	it in the meantime; nothing here changes `status`. """
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

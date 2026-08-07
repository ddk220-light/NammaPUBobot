__all__ = ['show_noadds', 'noadd', 'forgive', 'rating_seed', 'rating_hide', 'undo_match', 'identity_link', 'identity_unlink', 'identity_conflicts']

from time import time
from datetime import timedelta
from nextcord import Member, Embed, Colour
from nextcord.utils import escape_markdown

from nammaoe2bot.runtime.console import log
from nammaoe2bot.runtime.utils import seconds_to_str, get_nick

from nammaoe2bot.features.identity import resolver
from nammaoe2bot.exceptions import Exceptions as Exc
from nammaoe2bot.pickup import stats
from nammaoe2bot.pickup.noadds import noadds

# What each of resolver.CONFIDENCE_ORDER's tiers MEANS to the human reading
# `/identity show`. The bare lattice value is internal jargon, and the one
# question an admin is asking of it -- did a person decide this, or did a
# program guess it? -- is exactly what the raw word does not answer.
CONFIDENCE_GLOSS = {
	"seed": "seeded from the old mapping",
	"learned": "deduced automatically",
	"self": "linked by the player",
	"manual": "set by an admin",
}

# Discord hard-rejects an embed carrying more than 25 fields, so `/identity
# conflicts` lists at most 24 profiles and spends the 25th saying how many it
# left out. 24, not 25: the overflow notice is itself a field, and a listing
# that silently ends is worse than a shorter one that admits it did.
MAX_CONFLICT_FIELDS = 24


async def show_noadds(ctx):
	data = await noadds.get_noadds(ctx)
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
		raise Exc.ValueError(ctx.qc.gt("Specified duration time is too long."))
	await noadds.noadd(
		ctx=ctx, member=player, duration=int(duration.total_seconds()), moderator=ctx.author, reason=reason
	)
	await ctx.success(ctx.qc.gt("Banned **{member}** for `{duration}`.").format(
		member=get_nick(player),
		duration=duration.__str__()
	))


async def forgive(ctx, player: Member):
	ctx.check_perms(ctx.Perms.MODERATOR)
	if await noadds.forgive(ctx=ctx, member=player, moderator=ctx.author):
		await ctx.success(ctx.qc.gt("Done."))
	else:
		raise Exc.NotFoundError(ctx.qc.gt("Specified member is not banned."))


async def rating_seed(ctx, player: str, rating: int, deviation: int = None):
	ctx.check_perms(ctx.Perms.MODERATOR)
	if (player := await ctx.get_member(player)) is None:
		raise Exc.SyntaxError(f"Specified member not found on the server.")  # noqa: F541
	if not 0 < rating < 10000 or not 0 < (deviation or 1) < 3000:
		raise Exc.ValueError("Bad rating or deviation value.")

	await ctx.qc.rating.set_rating(player, rating=rating, deviation=deviation, reason="manual seeding")
	await ctx.qc.update_rating_roles(player)
	await ctx.success(ctx.qc.gt("Done."))


async def rating_hide(ctx, player: str, hide: bool = True):
	ctx.check_perms(ctx.Perms.MODERATOR)
	if (player := await ctx.get_member(player)) is None:
		raise Exc.SyntaxError(f"Specified member not found on the server.")  # noqa: F541
	await ctx.qc.rating.hide_player(player.id, hide=hide)
	await ctx.success(ctx.qc.gt("Done."))


async def undo_match(ctx, match_id: int):
	ctx.check_perms(ctx.Perms.MODERATOR)

	result = await stats.undo_match(ctx, match_id)
	if result:
		await ctx.success(ctx.qc.gt("Done."))
	else:
		raise Exc.NotFoundError(ctx.qc.gt("Could not find match with specified id."))


async def identity_link(ctx, member: Member, profile_id: int, additional: bool = False):
	""" The admin correction: bind `profile_id` to `member` at confidence
	`manual`, the tier no automated writer (seed, replay ingest, the deduction
	solver) can ever overwrite -- see resolver.learn's docstring.

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

	Both go through resolver.relink() rather than resolver.learn(), and both
	work on a profile that currently belongs to somebody ELSE -- that is what
	an admin correction IS, and it needs no confirmation flag. learn() would
	refuse it (equal tier, different user, identity v2's tie rule), file an
	`open` conflict against the admin's own instruction, and still reply
	"Linked" -- a false success. relink() is the one writer allowed to move a
	`manual` binding; the displaced owner and every released profile are
	recorded in identity_conflicts as `superseded` (spec section 3).

	The reply names what was actually taken away, because a release is
	destructive and otherwise invisible: an admin who meant `additional: true`
	needs the ids back to undo it.

	The id is validated against the AoE2 API BEFORE anything is written, exactly
	as the player's `/link` validates it (bot/commands/resolver.py). This is the
	higher-privilege command and it writes at the top tier, so it needs MORE
	checking, not less: a `manual` binding to a profile id that does not exist
	is one no automated writer can ever displace, and -- because the default
	shape releases everything else the member owned -- a single mistyped digit
	both creates that dead binding and unowns every real profile they had. The
	undo is lossy even when spotted (re-linking restores at `manual`, so
	whatever `self`/`learned` tier those profiles held is gone), which is why
	the check has to happen before the write rather than being something an
	admin corrects afterwards.

	The three outcomes are kept apart for the same reason `/link` keeps them
	apart: "no such profile" is the admin's typo to fix, while "the service is
	unreachable" says nothing at all about the id and must never send somebody
	hunting for a number that was already right.

	KNOWN COST, accepted: while the AoE2 profile service is down, this command
	cannot link at all, and there is deliberately no override — a `force` flag
	would restore exactly the hazard above and would get used precisely when an
	admin is in a hurry. The urgent half of an admin's job is still available:
	`/identity unlink` REMOVES a wrong link and touches no external service, so
	a bad binding can always be taken off immediately and replaced once the API
	answers again. """
	ctx.check_perms(ctx.Perms.ADMIN)
	# relink() coerces this too, but the release list below is compared against
	# ids read out of the DB, which are ints.
	profile_id = int(profile_id)
	if profile_id < 1:
		# Refused locally, before the API is even asked: fetch_profile maps the
		# 400 the service answers a negative id with to "unavailable", so
		# without this a number that cannot exist would be reported as the
		# SERVICE being broken (verified live 2026-07-30, see /link's guard).
		raise Exc.ValueError(ctx.qc.gt(
			"`{profile_id}` isn't a valid AoE2 profile id, so nothing was changed. "
			"Profile ids are positive numbers — check it on the player's "
			"aoe2insights.com page."
		).format(profile_id=profile_id))

	from nammaoe2bot.features.lobby import api as lobby_api

	status, data = await lobby_api.fetch_profile(profile_id)
	if status == "not_found":
		raise Exc.ValueError(ctx.qc.gt(
			"There's no AoE2 profile with the id `{profile_id}`, so nothing was changed. "
			"Check the number on the player's aoe2insights.com page and try again."
		).format(profile_id=profile_id))
	if status != "ok":
		# Deliberately does NOT blame the id: nothing here proves anything about
		# it, and saying otherwise would send an admin looking for a new number.
		raise Exc.ValueError(ctx.qc.gt(
			"I couldn't reach the AoE2 profile service just now, so nothing was changed. "
			"This isn't a problem with the id — please try again in a minute."
		))

	# From here on the API's own id is canonical, not the number that was typed
	# -- same rule as `/link`: it is what the service says the profile IS.
	profile_id = data["profile_id"]
	# `or None` matters: relink reads None as "not observed, keep the stored
	# name", while "" would blank a name somebody else's replay had recorded.
	observed_name = data["name"] or None

	# Both reads happen BEFORE the write -- afterwards the previous owner is
	# gone and the released profiles are no longer the member's.
	previous_owner = await resolver.user_for_profile(profile_id)
	owned = await resolver.profiles_for_users([member.id])
	others = sorted(pid for pid in owned.get(member.id, []) if pid != profile_id)

	await resolver.relink(profile_id, member.id, additional=additional, aoe2_name=observed_name)

	# The in-game name is echoed because it is the ONLY part of this reply an
	# admin can check against the person in front of them: a wrong-but-real id
	# passes every check above and produces an otherwise identical success.
	lines = [ctx.qc.gt(
		"Linked profile `{profile_id}`{name} to **{member}** as an additional account."
		if additional else
		"Linked profile `{profile_id}`{name} to **{member}**."
	).format(
		profile_id=profile_id,
		name=f" — {escape_markdown(data['name'])}" if data["name"] else "",
		member=get_nick(member),
	)]

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
		from nammaoe2bot.features.identity import solver

		await solver.run_for_channel(ctx.qc.id)
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

	owner = await resolver.user_for_profile(profile_id)
	if owner is None:
		raise Exc.ValueError(ctx.qc.gt(
			"Profile `{profile_id}` isn't linked to anyone, so there's nothing to unlink."
		).format(profile_id=profile_id))
	if owner != member.id:
		raise Exc.ValueError(ctx.qc.gt(
			"Profile `{profile_id}` is linked to <@{owner_id}>, not to **{member}**. "
			"Nothing was changed — re-run with the right member if you meant to remove that link."
		).format(profile_id=profile_id, owner_id=owner, member=get_nick(member)))

	await resolver.unlink(profile_id)

	# Deliberately does NOT say "they can claim it back with `/link`". `/link` is
	# view-only for anybody who already owns a profile (bot/commands/resolver.py
	# -- players may never CHANGE a link), so that advice is false for exactly
	# the population an admin unlinks most: multi-account players. The remedy
	# that always works is another admin link.
	await ctx.success(ctx.qc.gt(
		"Unlinked profile `{profile_id}` from **{member}**. It's unowned now. To give it "
		"back, run `/identity link` with `additional: True` — `/link` only works for a "
		"player who owns no profile at all."
	).format(profile_id=profile_id, member=get_nick(member)))


async def identity_conflicts(ctx):
	""" Read-only lookup: every open profile_id<->user_id claim that was not
	applied -- recorded instead of silently discarded by learn()/link_self()
	losing a lattice comparison, by migration 003_seed_identities, or by
	nammaoe2bot/features/identity/solver.py refusing to auto-apply its own conclusion (an unstable
	one, or one that would hand a member a second profile; those two show with no
	current owner, and `identity link ... additional: True` is how a genuine
	second account is granted). See nammaoe2bot/features/identity/resolver.py's identity_conflicts
	declaration and open_conflicts(). There is no resolution UI yet -- this just
	surfaces what open_conflicts() already tracks so a moderator isn't blind to
	it in the meantime; nothing here changes `status`.

	Capped at MAX_CONFLICT_FIELDS profiles. Discord rejects an embed with more
	than 25 fields outright, so an uncapped listing does not degrade — the
	command simply stops working, and it stops working precisely when there is
	the most to look at. The overflow is stated rather than swallowed: a
	moderator has to be able to tell "that is all of them" from "that is the
	first twenty-four". """
	ctx.check_perms(ctx.Perms.MODERATOR)

	conflicts = await resolver.open_conflicts()
	if not conflicts:
		await ctx.reply(ctx.qc.gt("no open identity conflicts"))
		return

	embed = Embed(title=ctx.qc.gt("Open identity conflicts"), colour=Colour(0x5865F2))
	# Lowest profile id first, so the truncated view is at least stable between
	# runs instead of reordering with whatever the DB returned.
	shown = sorted(conflicts, key=lambda c: c["profile_id"])[:MAX_CONFLICT_FIELDS]
	for c in shown:
		owner = f"<@{c['current_owner']}>" if c["current_owner"] is not None else ctx.qc.gt("(none known)")
		claimants = "\n".join(f"<@{claim['user_id']}> ({claim['source']})" for claim in c["claims"])
		embed.add_field(
			name=ctx.qc.gt("Profile `{profile_id}`").format(profile_id=c["profile_id"]),
			value=f"{ctx.qc.gt('Current owner')}: {owner}\n{ctx.qc.gt('Competing claim(s)')}:\n{claimants}",
			inline=False,
		)
	if len(conflicts) > len(shown):
		embed.add_field(
			name=ctx.qc.gt("And {omitted} more").format(omitted=len(conflicts) - len(shown)),
			value=ctx.qc.gt(
				"{total} profiles have open conflicts; only the first {shown} fit in one message. "
				"Settle some with `/identity link` and re-run this to see the rest."
			).format(total=len(conflicts), shown=len(shown)),
			inline=False,
		)
	await ctx.reply(embed=embed)


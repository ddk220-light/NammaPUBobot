"""Slash command and restart-independent component routing for quick civ picks."""
from nammaoe2bot.runtime.console import log
from . import picking, pick_store as store


async def civpick(ctx, match_id, minutes=3, redo=False):
	service = ctx.qc.app.civ_picker
	if service.throttled(ctx.author.id):
		return await ctx.reply('Please wait a second before trying again.', ephemeral=True)
	if not 1 <= minutes <= 10:
		return await ctx.reply('Choose 1–10 whole minutes.', ephemeral=True)
	# Always defer privately before database/Discord work; no public error posts.
	if not ctx.interaction.response.is_done():
		await ctx.interaction.response.defer(ephemeral=True)
	key = (ctx.qc.id, match_id)
	async with service.lock(key):
		try:
			existing = await store.get(*key)
			if not existing or redo:
				match = service.match(key)
				error = picking.validate_match(match)
				if error:
					raise ValueError(error)
				roster = picking.roster_for(match)
				service.validate(key, roster)
				admin = ctx.channel.permissions_for(ctx.author).manage_channels
				if not admin and ctx.author.id not in {p['id'] for p in roster}:
					raise ValueError('Only match participants or channel admins can start a round.')
				await store.start(*key, roster, ctx.author.id, minutes, redo, admin,
					validate=lambda: service.validate(key, roster))
			await store.change(*key, picking.expire)
			service.schedule(key)
			await service.refresh(key)
			row = await store.get(*key)
			url = f'https://discord.com/channels/{ctx.channel.guild.id}/{key[0]}/{row["message_id"]}'
			from .pick_view import card
			embed, _view = card(match_id, row['state'])
			await ctx.reply(f'[Civ-pick card]({url})', embed=embed, ephemeral=True)
		except ValueError as e:
			await ctx.reply(str(e), ephemeral=True)
		except Exception as e:
			log.error(f'Civ-pick command {key} failed: {e}')
			service.schedule(key)
			await ctx.reply('Could not update the civ-pick card. Run this command again to recover saved picks.',
				ephemeral=True)


async def on_interaction(interaction, app):
	data = interaction.data or {}
	cid = data.get('custom_id', '')
	if not cid.startswith('civpick:'):
		return
	try:
		parts = cid.split(':')
		if len(parts) != 4:
			return
		match_id, generation, choice = map(int, parts[1:])
		service = app.civ_picker
		if not app.ready or service.throttled(interaction.user.id):
			return await interaction.response.send_message('Please try again in a second.', ephemeral=True)
		await interaction.response.defer(ephemeral=True)
		key = (interaction.channel_id, match_id)
		async with service.lock(key):
			row = await store.get(*key)
			if not row or row['message_id'] != interaction.message.id:
				return await interaction.followup.send('Use the latest civ-pick card for this match.', ephemeral=True)
			def apply(state, now):
				if state['status'] == 'open':
					try:
						service.validate(key, state['roster'])
					except ValueError:
						state['status'] = 'invalidated'
				return picking.claim(state, interaction.user.id, choice, generation, now)
			error = await store.change(*key, apply)
			service.schedule(key)
			if not error:
				await service.refresh(key)
			await interaction.followup.send(error or 'Your pick is saved.', ephemeral=True)
	except Exception as e:
		log.error(f'Civ-pick interaction failed: {e}')
		if interaction.response.is_done():
			await interaction.followup.send('Could not update the card. Use /civpick to see saved picks.', ephemeral=True)
		else:
			await interaction.response.send_message('Could not read that civ-pick button.', ephemeral=True)

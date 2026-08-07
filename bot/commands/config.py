__all__ = ['create_pickup', 'delete_queue', 'show_queues', 'set_qc', 'set_queue', 'cfg_qc', 'cfg_queue']

import json
from nammaoe2bot.runtime.utils import find, get, split_big_text
from nammaoe2bot.runtime.console import log  # noqa: F401
from bot.exceptions import Exceptions as Exc
from bot.queues.pickup_queue import PickupQueue


async def create_pickup(ctx, name: str, size: int = 8):
	""" Create new PickupQueue """
	ctx.check_perms(ctx.Perms.ADMIN)
	try:
		pq = await ctx.qc.new_queue(ctx, name, size, PickupQueue)
	except ValueError as e:
		raise Exc.ValueError(str(e))
	else:
		await ctx.success(f"[**{pq.name}** ({pq.status})]")


async def delete_queue(ctx, queue: str):
	""" Delete a queue """
	ctx.check_perms(ctx.Perms.ADMIN)
	if (q := get(ctx.qc.queues, name=queue)) is None:
		raise Exc.NotFoundError(f"Queue '{queue}' not found on the channel..")
	await q.cfg.delete()
	ctx.qc.queues.remove(q)
	await show_queues(ctx)


async def show_queues(ctx):
	""" List all queues on the channel """
	if len(ctx.qc.queues):
		await ctx.reply("> [" + " | ".join(
			[f"**{q.name}** ({q.status})" for q in ctx.qc.queues]
		) + "]")
	else:
		await ctx.reply("> [ **no queues configured** ]")


async def set_qc(ctx, variable: str, value: str):
	""" Configure a QueueChannel variable """
	ctx.check_perms(ctx.Perms.ADMIN)

	if variable not in ctx.qc.cfg_factory.variables.keys():
		raise Exc.SyntaxError(f"No such variable '{variable}'.")
	try:
		await ctx.qc.cfg.update({variable: value})
	except Exception as e:
		raise Exc.ValueError(str(e))
	else:
		await ctx.success(f"Variable __{variable}__ configured.")


async def set_queue(ctx, queue: str, variable: str, value: str):
	""" Configure a Queue variable """
	ctx.check_perms(ctx.Perms.ADMIN)

	if (q := find(lambda i: i.name.lower() == queue.lower(), ctx.qc.queues)) is None:
		raise Exc.SyntaxError(f"Queue '{queue}' not found on the channel.")
	if variable not in q.cfg_factory.variables.keys():
		raise Exc.SyntaxError(f"No such variable '{variable}'.")

	try:
		await q.cfg.update({variable: value})
	except Exception as e:
		raise Exc.ValueError(str(e))
	else:
		await ctx.success(f"**{q.name}** variable __{variable}__ configured.")


async def cfg_qc(ctx):
	""" List QueueChannel configuration """
	await ctx.ignore("Sent channel configuration in DM.")  # Have to reply to the slash command
	gen = split_big_text(
		json.dumps(ctx.qc.cfg.readable(), ensure_ascii=False, indent=2),
		prefix="```json\n", suffix="\n```", limit=2000, delimiter=",\n"
	)
	for piece in gen:
		await ctx.reply_dm(piece)


async def cfg_queue(ctx, queue: str):
	""" List a queue configuration """
	if (q := find(lambda i: i.name.lower() == queue.lower(), ctx.qc.queues)) is None:
		raise Exc.SyntaxError(f"Queue '{queue}' not found on the channel.")
	await ctx.ignore(f"Sent **{queue}** configuration in DM.")  # Have to reply to the slash command
	gen = split_big_text(
		json.dumps(q.cfg.readable(), ensure_ascii=False, indent=2),
		prefix="```json\n", suffix="\n```", limit=2000, delimiter=",\n"
	)
	for piece in gen:
		await ctx.reply_dm(piece)


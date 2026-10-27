# Queue cards — 0.10.3

When the waiting roster changes, the bot reads the channel's five most recent public messages. If the current queue card is among them (including fifth), it edits that message in place. Otherwise it posts the updated card at the bottom, records the new message ID, then deletes the previous card. Other people's messages are never deleted. Private ephemeral messages are not part of channel history.

The same rule applies to explicit `/queue` refreshes and the empty waiting list after a match forms. Repeated Join or Leave clicks do not duplicate a player or publish another card. Successful **Join and Leave button** clicks receive a silent Discord acknowledgement; the public roster is the feedback. `/add`, `/remove` and other public updates clear their temporary slash response after success. Rejected actions still explain the problem privately. See the [complete interaction feedback review](interaction-feedback.md) for the retained private replies.

History is read only when a queue update is due. This adds an HTTP history request to that update, with no Discord Gateway listener or idle polling. The existing bot needs View Channel, Read Message History, Send Messages and Embed Links in the queue channel.

The durable outbox coalesces pending roster changes and retains its selected message ID across edit retries and restarts. History errors retry without deleting the old card or assuming it is too old. A missing card is recreated. Uncertain creates are reconciled before cleanup, and stale edit retries cannot overwrite the latest roster on a shared message. Rate limits and finite retry limits still apply; after repairing a blocked permission, `/queue` retries the saved publication.

Regression coverage lives in `tests/queue-recency.test.ts` and the signed-request tests in `tests/player-interactions.test.ts`: positions one/five/six, replacement ordering, edit and create receipt loss, restart recovery, deleted cards, history outages and rate limits, stale work, match formation, silent joins/leaves and private errors. Discord is mocked in these tests; actual client rendering requires a human button click.

No settings or schema migration is required. Existing queue membership, matches, ratings and wallets are unchanged by deployment.

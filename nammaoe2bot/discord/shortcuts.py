"""Pure parsing for the two retained text queue shortcuts."""
from __future__ import annotations

import unicodedata


_REMOVE_DASHES = frozenset((
	"--",  # ASCII keyboard
	"—",   # em dash: smart punctuation commonly produced from two hyphens
	"–",   # en dash
	"−",   # mathematical minus
))


def queue_shortcut_action(content: str | None) -> str | None:
	"""Return ``add``/``remove`` for an exact queue shortcut, otherwise None.

	NFKC accepts full-width keyboard variants without making a lone ASCII ``-``
	a command.  Unicode smart-dash forms are explicit because normalization does
	not fold them to ASCII hyphens.
	"""
	text = unicodedata.normalize("NFKC", str(content or "")).strip()
	if text == "++":
		return "add"
	if text in _REMOVE_DASHES:
		return "remove"
	return None

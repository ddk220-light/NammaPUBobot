# -*- coding: utf-8 -*-
"""
Typed configuration.

Backward-compatible with the original `config.cfg` flow (start.py writes that
file from environment variables on Railway, and it's still the primary source),
but adds three things ported from BombayBot's config approach:

  1. A typed schema with defaults — every key has a known type and a fallback,
     so a missing value yields a sane default instead of an AttributeError.
  2. Type coercion — ints/bools/lists are parsed, whether they come from
     config.cfg (already-typed Python literals) or from raw environment strings.
  3. Direct env-var fallback — any key absent from config.cfg is read straight
     from the environment, so the bot can run without start.py generating a file.

Precedence per key: config.cfg value > environment variable > default. This
preserves existing deploy behaviour exactly (config.cfg holds every key on
Railway) while making env-only runs and partial configs work cleanly.
"""
import os
import json
from importlib.machinery import SourceFileLoader

# key -> (type, default)
_SCHEMA = {
	'DC_BOT_TOKEN': (str, ""),
	'DC_CLIENT_ID': (int, 0),
	'DC_CLIENT_SECRET': (str, ""),
	'DC_INVITE_LINK': (str, ""),
	'DC_OWNER_ID': (int, 0),
	'DC_SLASH_SERVERS': (list, []),
	'PUBOBOT_USER_ID': (int, 0),
	'LOBBYBOT_USER_ID': (int, 0),
	'DB_URI': (str, ""),
	'LOG_LEVEL': (str, "INFO"),
	'COMMANDS_URL': (str, ""),
	'HELP': (str, ""),
	'STATUS': (str, ""),
	'WS_ENABLE': (bool, False),
	'WS_HOST': (str, ""),
	'WS_PORT': (int, 443),
	'WS_OAUTH_REDIRECT_URL': (str, ""),
	'WS_ROOT_URL': (str, ""),
	'WS_SSL_CERT_FILE': (str, ""),
	'WS_SSL_KEY_FILE': (str, ""),
}

_TRUE = ('1', 'true', 'yes', 'on')


def _coerce(value, typ):
	""" Coerce a config value (Python literal or env string) to `typ`. """
	if value is None:
		return None
	if typ is bool:
		if isinstance(value, bool):
			return value
		return str(value).strip().lower() in _TRUE
	if typ is int:
		if isinstance(value, bool):
			return int(value)
		return int(str(value).strip())
	if typ is list:
		if isinstance(value, (list, tuple)):
			return list(value)
		s = str(value).strip()
		if not s:
			return []
		try:
			parsed = json.loads(s)
			return parsed if isinstance(parsed, list) else [parsed]
		except (ValueError, TypeError):
			# Fallback: comma-separated, ints where possible (e.g. guild IDs).
			out = []
			for x in s.split(','):
				x = x.strip()
				if not x:
					continue
				out.append(int(x) if x.lstrip('-').isdigit() else x)
			return out
	return str(value)


class _Config:
	pass


cfg = _Config()

# Seed defaults so every schema key always exists on cfg.
for _key, (_typ, _default) in _SCHEMA.items():
	setattr(cfg, _key, _default)

# Primary source: config.cfg (Python source loaded via SourceFileLoader).
# Optional now — if absent we fall back to the environment. If present but
# malformed we still hard-fail, matching the original behaviour.
_file_cfg = {}
if os.path.exists('config.cfg'):
	try:
		_module = SourceFileLoader('cfg', 'config.cfg').load_module()
	except Exception as e:
		print("Failed to load config.cfg file!")
		raise e
	_file_cfg = {k: getattr(_module, k) for k in _SCHEMA if hasattr(_module, k)}

# Apply precedence: config.cfg > env var > default.
for _key, (_typ, _default) in _SCHEMA.items():
	if _key in _file_cfg:
		_val = _coerce(_file_cfg[_key], _typ)
	elif (_env := os.environ.get(_key)) is not None:
		_val = _coerce(_env, _typ)
	else:
		continue  # keep the seeded default
	if _val is not None:
		setattr(cfg, _key, _val)

# Soft validation — warn (don't crash) on missing essentials. start.py and
# PUBobot2.py already hard-validate these on the Railway path; this catches
# env-only or local misconfigurations without changing crash behaviour.
_missing = [k for k in ('DC_BOT_TOKEN', 'DB_URI') if not getattr(cfg, k)]
if _missing:
	print(f"WARNING: config is missing required values: {', '.join(_missing)}")

with open('.version', 'r') as f:
	__version__ = f.read()

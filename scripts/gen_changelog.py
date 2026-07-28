#!/usr/bin/env python3
"""Bake data/changelog.json from git history so /changelog works at runtime.

The runtime image (python:3.11-slim) has no git binary, and shelling out from a
Discord bot to read history would be the wrong shape anyway. So history is
snapshotted here at build time — see the Dockerfile — and the bot only ever
reads the resulting JSON.

Merge commits are dropped: "Merge pull request #34 from ..." says nothing about
what shipped. Everything else is kept in order, newest first.

Degrades quietly. If git is missing or .git was not copied into the build
context, this writes nothing and leaves any committed fallback in place, so a
build never fails over a changelog.
"""
import json
import os
import subprocess
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(REPO_ROOT, "data", "changelog.json")
KEEP = 25                      # bank more than /changelog shows so the count can grow
_SEP = "\x1f"                  # unit separator — safe inside commit subjects


def _git(*args):
	return subprocess.run(
		["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=30)


def collect(limit=KEEP):
	"""[{sha, subject, date}] newest-first, or None when history is unreadable."""
	# Prefer main; a Railway build checks out the deployed commit, which may be a
	# detached HEAD with no local main ref.
	for rev in ("main", "HEAD"):
		proc = _git("log", rev, "--no-merges", f"-{limit}",
					f"--pretty=format:%h{_SEP}%s{_SEP}%cs")
		if proc.returncode == 0 and proc.stdout.strip():
			entries = []
			for line in proc.stdout.strip().split("\n"):
				parts = line.split(_SEP)
				if len(parts) == 3:
					entries.append({"sha": parts[0], "subject": parts[1], "date": parts[2]})
			if entries:
				return entries
	return None


def main():
	try:
		entries = collect()
	except (OSError, subprocess.SubprocessError) as e:
		print(f"gen_changelog: git unavailable ({e}); leaving existing changelog in place.")
		return 0
	if not entries:
		print("gen_changelog: no history found; leaving existing changelog in place.")
		return 0
	os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
	with open(OUT_PATH, "w", encoding="utf-8") as fh:
		json.dump({"generated_at": int(time.time()), "entries": entries}, fh, indent=1)
		fh.write("\n")
	print(f"gen_changelog: wrote {len(entries)} entries to {OUT_PATH}")
	return 0


if __name__ == "__main__":
	sys.exit(main())

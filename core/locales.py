# Legacy gettext scaffolding, stubbed out in Layer 5 of the cleanup plan.
#
# The original codebase had a `locales/` dir with .po files for 7
# languages (ru, fr, community-it, community-pt-br, community-tr,
# community-es, community-ko) and a `compile_locales.sh` script that
# turned them into gettext .mo binaries at build time. That system:
#   - was broken on macOS/local dev because locales/compiled/ was
#     .gitignored and the msgfmt step was masked with `|| true`;
#   - was broken in practice everywhere because the community .po
#     files were never kept in sync with ru.po (the reference);
#   - supported zero real users — every deployed channel ran with
#     `cfg.lang = "en"` which resolved to `gettext.NullTranslations()`,
#     a pure identity function.
#
# So every one of the ~170 `self.gt("text")` call sites in bot/ was
# already a no-op at runtime. Deleting the translation layer entirely
# would touch 13 files and ~170 lines — too risky to bundle with other
# cleanup. Instead, we replace the loader with a passthrough singleton
# that preserves the `locales[cfg.lang](string)` call shape. Net effect:
#   - Import no longer listdirs a possibly-missing compiled/ dir.
#   - No gettext dependency.
#   - Any `cfg.lang` value (including legacy configs still on "ru")
#     returns the identity translator, so existing channels don't
#     break on startup.
#   - Future cleanup can inline `self.gt("x")` → `"x"` at leisure.


class _IdentityTranslator:
	"""Dict-like + callable stub. `locales["en"]("hello") == "hello"`."""

	def __getitem__(self, key):
		return self

	def __contains__(self, key):
		return True

	def get(self, key, default=None):
		return self

	def __call__(self, s):
		return s


locales = _IdentityTranslator()

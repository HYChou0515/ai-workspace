# Release tooling (#441). The changelog + GitHub Release + web /help/releases all
# derive from src/workspace_app/help_content/CHANGELOG.md, which is generated in
# house by `python -m workspace_app.changelog` — ONE bullet per merged PR, walked
# via `git log --first-parent`. See docs/releasing.md and the module docstring.
#
#   make changelog-preview   # dry-run: the section for commits since the newest tag
#   make release             # cut a CalVer release locally (bump + changelog + commit + tag)
#
# `release` is a MAINTAINER action: it bumps pyproject, regenerates the whole
# changelog (adding the new version section), commits "bump v…", and creates the
# local tag. It does NOT push — you then run `git push origin HEAD --follow-tags`,
# and the pushed tag triggers .github/workflows/release.yml to publish the GitHub
# Release. Nothing here is run in CI or by an agent; releasing stays a human step.
#
# Granularity: first-parent walk = one entry per PR-merge / squash / direct
# commit; the intermediate branch commits ("P1 …", "P2 …") never appear. The
# whole file is regenerated deterministically from the git tags every time, so
# the first (oldest) section folds the ENTIRE history and nothing is ever lost.

CHANGELOG_MODULE := workspace_app.changelog

.PHONY: changelog-preview
changelog-preview:
	@uv run python -m $(CHANGELOG_MODULE) --unreleased

.PHONY: release
release:
	@git diff --quiet && git diff --cached --quiet || { \
		echo "工作區不乾淨,請先 commit 或 stash 再 release"; exit 1; }
	@today="$$(date +%Y.%m.%d)"; base="v$$today"; \
	if git rev-parse -q --verify "refs/tags/$$base" >/dev/null; then \
		n=1; while git rev-parse -q --verify "refs/tags/$$base.$$n" >/dev/null; do n=$$((n+1)); done; \
		new="$$base.$$n"; \
	else new="$$base"; fi; \
	pep="$$(echo "$${new#v}" | sed -E 's/(^|\.)0+([0-9])/\1\2/g')"; \
	echo "release → $$new  (pyproject version $$pep)"; \
	sed -i "s/^version = .*/version = \"$$pep\"/" pyproject.toml; \
	uv lock --quiet; \
	uv run python -m $(CHANGELOG_MODULE) --release-version "$$new" --write; \
	git add pyproject.toml uv.lock src/workspace_app/help_content/CHANGELOG.md; \
	git commit -m "bump $$new"; \
	git tag "$$new"; \
	echo "✅ 已 commit \"bump $$new\" 並建立本地 tag $$new。接著執行:"; \
	echo "     git push origin HEAD --follow-tags"; \
	echo "   → .github/workflows/release.yml 會自動建立 GitHub Release。"

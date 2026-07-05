# Release tooling (#441). The changelog + GitHub Release + web /help/releases all
# derive from src/workspace_app/help_content/CHANGELOG.md, which git-cliff
# generates from Conventional Commits. See cliff.toml and docs/releasing.md.
#
#   make changelog-preview   # dry-run: what the next release section would contain
#   make release             # cut a CalVer release locally (bump + changelog + commit + tag)
#
# `release` is a MAINTAINER action: it bumps pyproject, folds unreleased commits
# into the changelog, commits "bump v…", and creates the local tag. It does NOT
# push — you then run `git push origin HEAD --follow-tags`, and the pushed tag
# triggers .github/workflows/release.yml to publish the GitHub Release. Nothing
# here is run in CI or by an agent; releasing stays a human step.
#
# Boundary: git-cliff's --unreleased bounds on the latest v[0-9]* tag
# (cliff.toml tag_pattern). Before the FIRST release (no such tag) it folds the
# ENTIRE history into the first section, so nothing done to date is lost. After
# that, each release spans only the commits since the previous tag.

CHANGELOG_FILE := src/workspace_app/help_content/CHANGELOG.md

.PHONY: changelog-preview
changelog-preview:
	@uv run git-cliff --unreleased

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
	uv run git-cliff --unreleased --tag "$$new" --prepend $(CHANGELOG_FILE); \
	git add pyproject.toml $(CHANGELOG_FILE); \
	git commit -m "bump $$new"; \
	git tag "$$new"; \
	echo "✅ 已 commit \"bump $$new\" 並建立本地 tag $$new。接著執行:"; \
	echo "     git push origin HEAD --follow-tags"; \
	echo "   → .github/workflows/release.yml 會自動建立 GitHub Release。"

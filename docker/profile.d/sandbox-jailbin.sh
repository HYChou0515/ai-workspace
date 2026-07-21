# Sandbox login-shell PATH guard.
#
# A sandbox that runs WITHOUT the userns jail (production: uid + cgroup only,
# #393) gets its `python`/`python3`/`pip` shims from a per-sandbox `.jailbin`
# dir that the exec path prepends to PATH. A login shell (`bash -lc …`, and the
# `sh -lc` every workflow node command is wrapped in) sources /etc/profile,
# which on Debian/Ubuntu HARD-RESETS PATH and throws that away -- routing the
# agent back to whatever interpreter the image ships, with none of the carrier's
# deps and none of its HOME rewriting.
#
# The dir is per-sandbox, so a pod-wide file cannot name it; it arrives in
# SANDBOX_JAILBIN, exported per-exec. /etc/profile resets PATH *only*, so
# exported variables survive it -- which is what makes this recoverable at all.
#
# The jail path solves the same problem by overlaying a tmpfs on /etc/profile.d
# from its bootstrap; unjailed there is no chroot to overlay, so this ships as a
# real file the sandbox images install.
if [ -n "${SANDBOX_JAILBIN:-}" ]; then
    PATH="$SANDBOX_JAILBIN:$PATH"
    export PATH
fi

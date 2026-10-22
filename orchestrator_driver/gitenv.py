"""Git settings and checks for processes that run outside the agent's sandbox (exec scripts, the worker's own
git calls).

An agent can write inside the repo's .git and the worktree, so neither its config nor its hooks are trusted: no
user or system config, no hooks, no fsmonitor, pager, ssh command, external diff or editor, no `ext::`
transport. Exec steps run no network git (the pack pushes from the sandboxed open-pr agent step), so the
credential helper is cleared too.

Config can only be planted if git is pointed at a git dir the agent made, so `check_git_links` also confirms,
before each exec step, that the worktree's `.git` pointer and `commondir` still lead into the project's own
git dir."""

import os
import subprocess
from pathlib import Path

GIT_OVERRIDES = {
    "core.hooksPath": "/dev/null",
    "core.fsmonitor": "",
    "core.pager": "cat",
    "core.sshCommand": "ssh",
    "diff.external": "",
    "core.editor": "true",
    "credential.helper": "",
    "protocol.ext.allow": "never",
}

GIT_ENV = {
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_COUNT": str(len(GIT_OVERRIDES)),
    **{f"GIT_CONFIG_KEY_{i}": k for i, k in enumerate(GIT_OVERRIDES)},
    **{f"GIT_CONFIG_VALUE_{i}": v for i, v in enumerate(GIT_OVERRIDES.values())},
    "GIT_PAGER": "cat",
    "GIT_TERMINAL_PROMPT": "0",
}

_IDENTITY: dict[str, str] | None = None


def identity_env() -> dict[str, str]:
    """user.name / user.email from the machine's own global config, as author and committer variables: the
    global config itself is off, but a merge commit still needs to know who makes it."""
    global _IDENTITY
    if _IDENTITY is None:
        found = {}
        for key, var in (("user.name", "NAME"), ("user.email", "EMAIL")):
            r = subprocess.run(
                ["git", "config", "--global", key], capture_output=True, text=True, check=False, env=os.environ
            )
            if r.returncode == 0 and r.stdout.strip():
                found.update({f"GIT_AUTHOR_{var}": r.stdout.strip(), f"GIT_COMMITTER_{var}": r.stdout.strip()})
        _IDENTITY = found
    return {k: v for k, v in _IDENTITY.items() if k not in os.environ}


def safe_env() -> dict[str, str]:
    """GIT_ENV plus the machine's commit identity: what unsandboxed git runs with."""
    return {**identity_env(), **GIT_ENV}


class GitLinkError(RuntimeError):
    pass


def _real(base: Path, text: str) -> str:
    return os.path.realpath(base / text.strip())  # an absolute `text` wins over base


def _read(p: Path) -> str | None:
    try:
        return p.read_text()
    except (OSError, UnicodeDecodeError):
        return None


def check_git_links(worktree: str, repo_root: str) -> None:
    """Raise GitLinkError unless `worktree` is the project itself or a linked worktree whose `.git` pointer,
    `commondir` and back-pointer all resolve into the project's git dir (compared as real paths)."""
    if not worktree or not repo_root:
        return
    wt, repo = Path(worktree), Path(repo_root)
    if os.path.realpath(wt) == os.path.realpath(repo):
        return
    common = Path(repo, ".git")
    if not common.is_dir():
        raise GitLinkError(f"{repo_root} has no .git directory: refusing to run git for the worktree {worktree}")
    common_real = os.path.realpath(common)
    dot = wt / ".git"
    if dot.is_dir():
        raise GitLinkError(f"{dot} is a git directory, not a pointer into {common_real}: refusing to run git")
    text = _read(dot) if dot.is_file() else None
    if text is None or not text.startswith("gitdir:"):
        raise GitLinkError(f"{dot} is not a 'gitdir:' pointer: refusing to run git")
    gitdir = _real(wt, text.split(":", 1)[1])
    if os.path.dirname(gitdir) != os.path.join(common_real, "worktrees"):
        raise GitLinkError(f"{dot} points at {gitdir}, outside {common_real}/worktrees: refusing to run git")
    cd = _read(Path(gitdir, "commondir"))
    if cd is None or _real(Path(gitdir), cd) != common_real:
        raise GitLinkError(f"{gitdir}/commondir does not resolve to {common_real}: refusing to run git")
    back = _read(Path(gitdir, "gitdir"))
    if back is None or _real(Path(gitdir), back) != os.path.realpath(dot):
        raise GitLinkError(f"{gitdir}/gitdir does not point back at {dot}: refusing to run git")

"""Link-integrity: paths mentioned in skill/prompt charters must resolve."""
from __future__ import annotations

import re
from pathlib import Path

from orchestrator_next.paths import config_root

_PATH_RE = re.compile(r"\$ORCHESTRATOR_CONFIG/steps/[A-Za-z0-9_\-./]+\.md")
_SKILLS_PATH_RE = re.compile(r"skills/[A-Za-z0-9_\-./]+\.md")


def test_referenced_step_paths_exist():
    root = config_root()
    repo = root.parent if root.name == "config" else root
    missing = []
    prompts = [
        *(root / "steps").glob("*/prompt.md"),
        *(root / "steps").glob("*/pack/prompt.md"),
        *(root / "steps").glob("*/pack/SKILL.md"),
        *(root / "steps").glob("*/*/SKILL.md"),  # steps/<id>/<id>/SKILL.md symlink
        *Path(repo / "skills").glob("*/SKILL.md"),
    ]
    for prompt_path in sorted(prompts):
        if not prompt_path.is_file():
            continue
        text = prompt_path.read_text()
        for match in _PATH_RE.findall(text):
            rel = match.removeprefix("$ORCHESTRATOR_CONFIG/")
            if not (root / rel).is_file():
                missing.append(f"{prompt_path}: {match}")
        for match in _SKILLS_PATH_RE.findall(text):
            if not (repo / match).is_file():
                missing.append(f"{prompt_path}: {match}")
    assert not missing, "broken charter reference(s):\n" + "\n".join(missing)

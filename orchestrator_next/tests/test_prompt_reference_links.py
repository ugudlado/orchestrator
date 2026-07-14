"""C3 link-integrity: every $ORCHESTRATOR_CONFIG/steps/... path mentioned in a
prompt.md must resolve to a real file, or a Read-on-demand reference silently
degrades into an agent improvising the missing content."""
from __future__ import annotations

import re

from orchestrator_next.paths import config_root

_PATH_RE = re.compile(r"\$ORCHESTRATOR_CONFIG/steps/[A-Za-z0-9_\-./]+\.md")


def test_referenced_step_paths_exist():
    root = config_root()
    missing = []
    for prompt_path in sorted((root / "steps").glob("*/prompt.md")):
        text = prompt_path.read_text()
        for match in _PATH_RE.findall(text):
            rel = match.removeprefix("$ORCHESTRATOR_CONFIG/")
            if not (root / rel).is_file():
                missing.append(f"{prompt_path}: {match}")
    assert not missing, "broken $ORCHESTRATOR_CONFIG reference(s):\n" + "\n".join(missing)

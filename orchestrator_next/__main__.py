"""Console-script entry point for ``poetry run orchestrator`` / ``python -m orchestrator_next``.

The real CLI logic lives in the standalone ``bin/orchestrator`` script (it predates
packaging and does its own sys.path setup). Rather than duplicate ~280 lines, this
shim loads that script by path and calls its ``main()``. The pyproject entry point
``orchestrator = "orchestrator_next.__main__:main"`` resolves here.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from importlib.machinery import SourceFileLoader


def _load_bin_main():
    # orchestrator_next/__main__.py -> repo root -> bin/orchestrator
    pkg_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(pkg_dir)
    bin_path = os.path.join(repo_root, "bin", "orchestrator")
    if not os.path.isfile(bin_path):
        sys.stderr.write(f"orchestrator: cannot find CLI script at {bin_path}\n")
        sys.exit(3)
    # bin/orchestrator has no .py suffix, so give importlib an explicit source loader.
    loader = SourceFileLoader("_orchestrator_bin", bin_path)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module.main


def main() -> None:
    _load_bin_main()()


if __name__ == "__main__":
    main()

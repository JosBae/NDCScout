"""NDC Scout: rank NDCs of a generic drug by pharmacy reimbursement margin."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=False)  # never clobber a key already exported in the shell

__version__ = "0.1.0"


def resource_root() -> Path:
    """Directory holding skills/, web/ and sample_data/.

    Order: $NDC_SCOUT_ROOT, the source checkout (editable install), then the
    current directory (a regular `pip install .` run from the repo).
    """
    candidates = [os.environ.get("NDC_SCOUT_ROOT"), Path(__file__).resolve().parent.parent, Path.cwd()]
    for c in candidates:
        if c and (Path(c) / "skills" / "ndc-reimbursement" / "SKILL.md").exists():
            return Path(c)
    raise FileNotFoundError("Cannot find skills/, web/ and sample_data/. Run from the repo folder or set NDC_SCOUT_ROOT.")

from __future__ import annotations

import sys
from pathlib import Path

_src = Path(__file__).resolve().parent.parent
_repo_root = _src.parent

if _src.exists() and str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from cli.main import main

if __name__ == "__main__":
    sys.exit(main())

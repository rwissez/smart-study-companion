"""``python -m study_companion.sources`` 的只读审计入口。"""

from __future__ import annotations

import sys

from .audit import main

if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

"""让 `pytest -q`（不需要 `python -m pytest`）也能把仓库根目录放进 sys.path。

CI 里直接调用 `pytest`，此时 cwd 不一定会被 pytest 自动加入 sys.path，
导致 `import backend.app...` 失败。这里显式插入仓库根目录。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

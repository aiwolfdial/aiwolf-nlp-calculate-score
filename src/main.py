"""エントリポイント。`uv run src/main.py <コマンド> ...` で実行する。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from aiwolf_nlp_calculate_score.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())

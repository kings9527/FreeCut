"""Entry point used by the PyInstaller build (and by ``python run_freecut.py``).

Keeping this separate means the frozen executable has a tiny, predictable
``__main__``: it adds nothing but a dispatch into :func:`freecut.cli.main`.
"""

from __future__ import annotations

import sys


def main() -> int:
    try:
        from freecut.cli import main as cli_main
    except Exception as exc:  # pragma: no cover - only on a broken install
        print("FreeCut 启动失败:", exc)
        print("请确认已安装依赖：pip install -r requirements.txt")
        return 1
    return int(cli_main(sys.argv[1:]) or 0)


if __name__ == "__main__":
    raise SystemExit(main())

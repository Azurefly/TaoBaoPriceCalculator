"""PyInstaller entrypoint; also supports source-mode desktop development."""
import sys
from pathlib import Path
if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from desktop.service import main

if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    raise SystemExit(main())

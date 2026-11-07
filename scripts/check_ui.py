"""Compatibility entry: test the current default (simple) interface."""
import runpy
from pathlib import Path

if __name__ == '__main__':
    runpy.run_path(str(Path(__file__).with_name('check_simple_ui.py')), run_name='__main__')

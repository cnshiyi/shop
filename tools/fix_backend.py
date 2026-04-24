"""
兼容入口：历史脚本已迁到 tools/legacy/fix_backend.py
"""

from pathlib import Path
import runpy


if __name__ == '__main__':
    runpy.run_path(str(Path(__file__).resolve().parent / 'legacy' / 'fix_backend.py'), run_name='__main__')

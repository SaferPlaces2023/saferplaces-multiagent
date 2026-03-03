"""
T002.py
-------
Run:
    python -m tests.T002
    python -m tests.T002 "Run a flood simulation for Rome"
"""

import sys
from pathlib import Path
from tests._utils import run_tests, load_test_config

_CFG = load_test_config("T002")
RESULT_FILE = Path(__file__).parent / "result" / "T002.md"

if __name__ == "__main__":
    cli_messages = sys.argv[1:]
    run_tests(cli_messages if cli_messages else _CFG["TEST_MESSAGES"], result_file=RESULT_FILE)

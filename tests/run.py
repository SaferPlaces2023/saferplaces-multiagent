"""
run.py
------
Universal test runner. Loads config from tests.json by test ID.

Run:
    python -m tests.run T001
    python -m tests.run T002
"""

import sys
from pathlib import Path
from tests._utils import run_tests, load_test_config

def main():
    if len(sys.argv) < 2:
        # List available test suites
        import json
        tests_json = Path(__file__).parent / "tests.json"
        with tests_json.open(encoding="utf-8") as f:
            all_tests = json.load(f)
        print("Available test suites:")
        for tid, cfg in all_tests.items():
            print(f"  {tid}  {cfg['title']} — {cfg['description']}")
        print("\nUsage: python -m tests.run <TEST_ID>")
        sys.exit(0)

    test_id = sys.argv[1].upper()
    cfg = load_test_config(test_id)

    result_file = Path(__file__).parent / "result" / f"{test_id}.md"

    run_tests(cfg["TEST_MESSAGES"], result_file=result_file)

if __name__ == "__main__":
    main()

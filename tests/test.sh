#!/bin/bash
set -euo pipefail
mkdir -p /logs/verifier
printf '0\n' > /logs/verifier/reward.txt
cd /tests/wheels
sha256sum -c /tests/wheels-sha256.txt
/usr/local/bin/python -I -m pip install --no-index --no-deps --find-links /tests/wheels -r /tests/requirements-test.txt
# -I rejects PYTHONPATH, user-site packages and cwd import poisoning.
exec /usr/local/bin/python -I /tests/grade.py

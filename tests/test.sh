#!/bin/bash
set -euo pipefail
mkdir -p /logs/verifier
printf '0\n' > /logs/verifier/reward.txt
cd /tests/wheels
sha256sum -c /tests/wheels-sha256.txt
/usr/local/bin/python -I -m pip install --no-index --no-deps --find-links /tests/wheels -r /tests/requirements-test.txt
# The supervisor launches this pytest executable in a clean environment and
# records every collected identity and setup/call/teardown result before scoring.
exec /usr/local/bin/python -I /tests/grade.py /usr/local/bin/pytest

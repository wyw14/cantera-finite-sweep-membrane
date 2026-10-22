#!/bin/bash
set -euo pipefail
umask 077
reward=/logs/verifier/reward.txt
JUNIT_REPORT=/logs/verifier/junit.xml
workspace=
stage=bootstrap
pytest_status=125
finalized=0
active_group=
watchdog_group=

run_stage() {
    # Waiting on an asynchronous child lets shell signal traps run promptly.
    # Each trusted stage owns a process group, including its subprocesses.
    local status=0
    setsid "$@" &
    active_group=$!
    wait "$active_group" || status=$?
    active_group=
    return "$status"
}

stop_active_stage() {
    if [[ -n "$active_group" ]]; then
        kill -KILL -- "-$active_group" 2>/dev/null || true
        wait "$active_group" 2>/dev/null || true
        active_group=
    fi
    # Candidate RPC workers own separate sessions. Cleanup must remain
    # possible even when Python finalization itself is unavailable.
    if [[ -n "$workspace" && -d "$workspace" ]]; then
        local ledger="$workspace/candidate-pids.txt" pid
        if [[ -f "$ledger" && ! -L "$ledger" ]]; then
            while IFS= read -r pid; do
                if [[ "$pid" =~ ^[1-9][0-9]*$ ]]; then
                    kill -KILL -- "-$pid" 2>/dev/null || true
                fi
            done < "$ledger"
        fi
    fi
}

reset_reward() {
    # This path deliberately does not depend on Python or successful imports.
    # No candidate runs before these root-owned directories are protected.
    local temporary unsafe=0
    if [[ "$EUID" != 0 || -L /logs || -L /logs/verifier ]]; then
        printf '%s\n' 'Unsafe verifier output path or non-root bootstrap' >&2
        return 1
    fi
    mkdir -p /logs/verifier || return 1
    chown 0:0 /logs /logs/verifier || return 1
    chmod 0700 /logs /logs/verifier || return 1
    if [[ -L "$reward" || ( -e "$reward" && ! -f "$reward" ) ]]; then
        # Replace the directory entry without following it. Still reject this
        # run, but do not leave a symlink through which stale 1 could be read.
        unsafe=1
    fi
    temporary="$(mktemp /logs/verifier/.reward-bootstrap-XXXXXXXX)" || return 1
    if ! printf '0\n' > "$temporary" || ! chmod 0600 "$temporary"; then
        rm -f -- "$temporary"
        return 1
    fi
    # Windows-backed mounts may expose 0444 as a read-only attribute. Only
    # root can enter the protected directory; clear that bit before replace.
    if [[ -e "$reward" && ! -L "$reward" && -f "$reward" ]] && ! chmod 0600 "$reward"; then
        rm -f -- "$temporary"
        return 1
    fi
    if ! mv -fT -- "$temporary" "$reward"; then
        rm -f -- "$temporary"
        return 1
    fi
    if [[ "$unsafe" != 0 ]]; then
        printf '%s\n' 'Unsafe reward entry replaced with zero; rejecting this run' >&2
        return 1
    fi
}

seal_reward() {
    if [[ -L "$reward" || ! -f "$reward" ]]; then
        printf '%s\n' 'Cannot seal a missing or unsafe reward output' >&2
        return 1
    fi
    # Once the trusted shell has finished, nobody needs to write this result.
    chmod 0444 "$reward"
}

clear_reports() {
    # Reports from an earlier run cannot establish completion of this run.
    local path
    for path in /logs/verifier/junit.xml /logs/verifier/outcomes.json \
        /logs/verifier/grading.json /logs/verifier/completion.json \
        /logs/verifier/preflight.json /logs/verifier/pytest.log; do
        if [[ -L "$path" || ( -e "$path" && ! -f "$path" ) ]]; then
            printf 'Unsafe existing verifier report: %s\n' "$path" >&2
            return 1
        fi
        if [[ -f "$path" ]]; then
            chmod 0600 "$path" || return 1
            rm -- "$path" || return 1
        fi
    done
}

finish() {
    local shell_status=$?
    trap - EXIT HUP INT TERM
    set +e
    stop_active_stage
    if [[ -n "$watchdog_group" ]]; then
        kill -KILL -- "-$watchdog_group" 2>/dev/null || true
        wait "$watchdog_group" 2>/dev/null || true
    fi
    if [[ "$shell_status" != 0 ]]; then
        if [[ "$stage" != interrupted ]]; then
            stage=shell_failure
        fi
        pytest_status="$shell_status"
        finalized=0
    fi
    if [[ "$finalized" != 1 ]]; then
        printf 'INCOMPLETE: verifier did not finish during %s\n' "$stage" >&2
        reset_reward || shell_status=1
        if [[ -n "$workspace" && -d "$workspace" ]]; then
            if ! /usr/bin/timeout --signal=TERM --kill-after=1s 2s \
                /usr/local/bin/python -I /tests/grade.py finalize "$workspace" \
                    --pytest-status "$pytest_status" --stage "$stage" \
                    --junitxml "$JUNIT_REPORT"; then
                reset_reward || shell_status=1
            fi
        fi
    fi
    if ! seal_reward; then
        printf '%s\n' 'Failed to seal the result; forcing failure' >&2
        reset_reward
        shell_status=1
    fi
    exit "$shell_status"
}
interrupt() {
    stage=interrupted
    pytest_status="$1"
    finalized=0
    exit "$1"
}
trap finish EXIT
trap 'interrupt 129' HUP
trap 'interrupt 130' INT
trap 'interrupt 143' TERM

# Leave time to preserve a failed result before Harbor's 300-second limit;
# the scientific pytest process retains its 270-second allowance.
setsid /bin/bash -c 'sleep 295; kill -TERM "$1"' watchdog "$$" &
watchdog_group=$!
reset_reward
clear_reports
run_stage /usr/bin/timeout --signal=TERM --kill-after=2s 15s \
    /usr/local/bin/python -I /tests/grade.py initialize
workspace="$(mktemp -d /tmp/trusted-verifier-XXXXXXXX)"
chmod 700 "$workspace"
stage=prepare
run_stage /usr/bin/timeout --signal=TERM --kill-after=2s 15s \
    /usr/local/bin/python -I /tests/grade.py prepare "$workspace"
stage=install_dependencies
cd /tests/wheels
sha256sum -c /tests/wheels-sha256.txt
run_stage /usr/bin/timeout --signal=TERM --kill-after=2s 15s \
    /usr/local/bin/python -I -m pip install --no-index --no-deps \
    --find-links /tests/wheels -r /tests/requirements-test.txt

stage=pytest
cd /
run_stage /usr/bin/timeout --signal=TERM --kill-after=2s 278s \
    /bin/bash -c '
set +e
workspace="$1"
JUNIT_REPORT="$2"
env -i PATH=/usr/local/bin:/usr/bin:/bin LANG=C.UTF-8 HOME="$workspace" \
    MPLCONFIGDIR="$workspace/mpl" MPLBACKEND=Agg OPENBLAS_NUM_THREADS=1 \
    OMP_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
    PYTHONSAFEPATH=1 PYTHONPATH=/tests PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
    CANDIDATE_PID_LEDGER="$workspace/candidate-pids.txt" \
    /usr/bin/timeout --signal=TERM --kill-after=5s 270s \
    /usr/local/bin/pytest /tests/test_reactor.py -q --tb=short \
    -p no:cacheprovider -p trusted_runner --trusted-ledger "$workspace/outcomes.json" \
    --junitxml="$JUNIT_REPORT" \
    -c /tests/pytest.ini --confcutdir=/tests --rootdir=/tests \
    2>&1 | tee /logs/verifier/pytest.log
statuses=("${PIPESTATUS[@]}")
printf "%s\\n" "${statuses[@]}" > "$workspace/stage-status.txt"
exit 0
' trusted-pytest "$workspace" "$JUNIT_REPORT"
mapfile -t statuses < "$workspace/stage-status.txt"
pytest_status="${statuses[0]}"
if [[ "${statuses[1]}" != 0 ]]; then
    stage=log_capture
fi
run_stage /usr/bin/timeout --signal=TERM --kill-after=2s 15s \
    /usr/local/bin/python -I /tests/grade.py finalize "$workspace" \
    --pytest-status "$pytest_status" --stage "$stage" --junitxml "$JUNIT_REPORT"
stage=completion_validation
test -s "$JUNIT_REPORT"
test -s /logs/verifier/outcomes.json
test -s /logs/verifier/grading.json
# Re-open the actual pytest report in the outer supervisor. This independent
# seal checks report identity, every test phase, and the published reward.
run_stage /usr/bin/timeout --signal=TERM --kill-after=1s 5s \
    /usr/local/bin/python -I -c '
import sys
from pathlib import Path
import xml.etree.ElementTree as ET
sys.path.insert(0, "/tests")
from grade import write_json
from validate_completion import validate_seal
logs = Path("/logs/verifier")
try:
    tree = ET.parse(sys.argv[1])
    record = validate_seal(Path("/tests/test-contract.json"), logs, tree)
except Exception as error:
    record = {"completion_state": "INCOMPLETE", "error": str(error)}
    write_json(logs / "completion.json", record)
    print("INCOMPLETE: " + str(error), file=sys.stderr)
    raise SystemExit(1)
write_json(logs / "completion.json", record)
' "$JUNIT_REPORT"
finalized=1

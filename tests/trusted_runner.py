"""Record trusted pytest CLI results; candidate code executes only through RPC."""

import json
import sys
from pathlib import Path


class Ledger:
    """Keep exact test identities and all three phases for the score supervisor."""

    def __init__(self, output):
        """Initialize a private, root-owned outcome destination."""
        self.output = Path(output)
        self.collected = []
        self.outcomes = {}
        self.collection_errors = []
        self.failures = []
        self.warnings = []

    def pytest_collection_finish(self, session):
        """Record exact identities after collection has completed."""
        self.collected = [item.nodeid for item in session.items]

    def pytest_collectreport(self, report):
        """Keep collection errors separate from candidate assertion failures."""
        if report.failed:
            self.collection_errors.append(report.nodeid)

    def pytest_runtest_logreport(self, report):
        """Record each setup, call, and teardown outcome without filtering."""
        self.outcomes.setdefault(report.nodeid, []).append(
            [report.when, report.outcome]
        )
        if report.failed:
            self.failures.append(
                {
                    "nodeid": report.nodeid,
                    "phase": report.when,
                    "outcome": report.outcome,
                }
            )

    def pytest_warning_recorded(self, warning_message, when, nodeid, location):
        """Keep observed warnings as diagnostics without changing acceptance."""
        self.warnings.append(
            {"message": str(warning_message.message), "when": when, "nodeid": nodeid}
        )

    def pytest_sessionfinish(self, session, exitstatus):
        """Write the complete session ledger for the independent finalizer."""
        self.output.write_text(
            json.dumps(
                {
                    "collected": self.collected,
                    "outcomes": self.outcomes,
                    "collection_errors": self.collection_errors,
                    "exitstatus": int(exitstatus),
                    "execution_errors": list(sys.modules["candidate_proxy"].EXECUTION_ERRORS),
                    "failures": self.failures,
                    "warnings": self.warnings,
                },
                indent=2,
            ),
            encoding="utf-8",
        )


def pytest_addoption(parser):
    """Require an explicit private ledger path when loading this plugin."""
    parser.addoption(
        "--trusted-ledger", required=True, help="Private verifier outcome ledger"
    )


def pytest_configure(config):
    """Register the trusted ledger for the ordinary pytest executable."""
    config.pluginmanager.register(
        Ledger(config.getoption("--trusted-ledger")), "trusted-ledger"
    )

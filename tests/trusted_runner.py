"""Record trusted pytest CLI results; candidate code executes only through RPC."""

import json
from pathlib import Path


class Ledger:
    """Keep exact test identities and all three phases for the score supervisor."""

    def __init__(self, output):
        self.output = Path(output)
        self.collected = []
        self.outcomes = {}
        self.collection_errors = []

    def pytest_collection_finish(self, session):
        self.collected = [item.nodeid for item in session.items]

    def pytest_collectreport(self, report):
        if report.failed:
            self.collection_errors.append(report.nodeid)

    def pytest_runtest_logreport(self, report):
        self.outcomes.setdefault(report.nodeid, []).append([report.when, report.outcome])

    def pytest_sessionfinish(self, session, exitstatus):
        self.output.write_text(json.dumps({'collected': self.collected,
            'outcomes': self.outcomes, 'collection_errors': self.collection_errors,
            'exitstatus': int(exitstatus)}, indent=2), encoding='utf-8')

def pytest_addoption(parser):
    parser.addoption('--trusted-ledger', required=True, help='Private verifier outcome ledger')


def pytest_configure(config):
    config.pluginmanager.register(Ledger(config.getoption('--trusted-ledger')), 'trusted-ledger')

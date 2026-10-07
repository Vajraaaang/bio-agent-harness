"""Receipt versions and execution attribution, with no provider calls."""
from contextlib import contextmanager
import json
from pathlib import Path
import tempfile

import pytest

from devbench.study.broker import BrokerPolicy, ToolBroker
from devbench.study.evidence import verify_receipts


class FixtureBroker(ToolBroker):
    @property
    def allowed_tools(self):
        return frozenset({'fixture_count'})

    def _invoke_tool(self, tool, arguments):
        return {'value': arguments['value']}


@contextmanager
def broker_fixture():
    root = Path(__file__).resolve().parents[1] / 'tmp'
    root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='execution-receipts-', dir=root) as directory:
        base = Path(directory)
        source = base / 'input.json'
        source.write_text('{"constant":1}')
        policy = BrokerPolicy('enforced', 'a' * 64, 'raw_counts', ('g',), False, 30, 'b' * 64)
        broker = FixtureBroker(policy, {str(source): source}, base / 'receipts', allow_data_execution=True)
        yield broker


def context(number):
    return {'execution_id': f'execution-{number:04d}', 'sequence': number,
            'cli_item_id': f'item-{number}', 'command_sha256': 'c' * 64,
            'start_observed': True}


def complete(broker, item):
    broker.observe_execution('completed', {**item, 'direct_script_invocation': True,
        'succeeded': True, 'exit_code': 0, 'script_sha256_at_completion': 'd' * 64,
        'answer_sha256_at_completion': 'e' * 64})


def test_repeated_real_requests_remain_counted_but_have_distinct_executions():
    with broker_fixture() as broker:
        assert callable(getattr(broker, 'enable_execution_scopes', None))
        broker.enable_execution_scopes()
        for number in (1, 2):
            item = context(number)
            broker.observe_execution('started', item)
            for _ in range(5):
                assert broker.execute('fixture_count', {'value': 7})['status'] == 'succeeded'
            complete(broker, item)
        root = broker.close()
        assert root['record_format'] == 'devbench.study.tool-receipts/2'
        assert root['event_count'] == 10
        verified = verify_receipts(broker.output_dir, expected_receipt=root,
            expected_input_sha256='a' * 64, expected_implementation_sha256='b' * 64,
            allow_evidence_read=True)
        assert len(verified.executions) == 2
        assert sum(e['execution_id'] == 'execution-0002' for e in verified.events) == 5
        assert len({e['tool'] for e in verified.events}) == 1


def test_changed_execution_manifest_cannot_pass_retained_root_verification():
    with broker_fixture() as broker:
        assert callable(getattr(broker, 'enable_execution_scopes', None))
        broker.enable_execution_scopes()
        item = context(1)
        broker.observe_execution('started', item)
        broker.execute('fixture_count', {'value': 7})
        complete(broker, item)
        root = broker.close()
        file = broker.output_dir / 'executions.json'
        file.write_text('[]')
        with pytest.raises(ValueError, match='execution|Execution'):
            verify_receipts(broker.output_dir, expected_receipt=root,
                expected_input_sha256='a' * 64, expected_implementation_sha256='b' * 64,
                allow_evidence_read=True)


def test_overlapping_command_windows_fail_closed():
    with broker_fixture() as broker:
        assert callable(getattr(broker, 'enable_execution_scopes', None))
        broker.enable_execution_scopes()
        broker.observe_execution('started', context(1))
        with pytest.raises(ValueError, match='overlap|active'):
            broker.observe_execution('started', context(2))
        assert broker.close()['capture_complete'] is False

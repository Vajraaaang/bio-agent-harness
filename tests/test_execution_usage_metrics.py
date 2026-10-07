"""Adoption is a participant rate, not an iteration-inflated call count."""
from types import SimpleNamespace

from devbench.study.evidence import VerifiedReceipts
from devbench.study.execution_usage import selected_tool_calls, tool_usage_metrics


def row(calls, selected, *, captured=True):
    return SimpleNamespace(attempted=True, trusted_tool_capture_complete=captured,
                           trusted_tool_calls=calls, final_execution_tool_calls=selected)


def test_extra_iterations_change_resource_counts_but_not_adoption():
    first = tool_usage_metrics([row(5, 5)])
    repeated = tool_usage_metrics([row(10, 5)])
    assert first['tool_adoption_rate'] == repeated['tool_adoption_rate'] == 1.0
    assert first['tool_adoption_count'] == repeated['tool_adoption_count'] == 1
    assert repeated['tool_calls_all_executions_known_subtotal'] == 10
    assert repeated['final_execution_tool_calls_known_subtotal'] == 5


def test_unknown_capture_is_disclosed_and_never_assumed_nonuse():
    result = tool_usage_metrics([row(10, 5), row(None, None, captured=False), row(0, 0)])
    assert result['tool_adoption_count'] == 1
    assert result['tool_adoption_denominator'] == 2
    assert result['tool_adoption_unknown_count'] == 1
    assert result['tool_adoption_rate'] == 0.5


def test_legacy_receipts_do_not_gain_invented_execution_attribution():
    legacy = VerifiedReceipts('a'*64, 'b'*64, 'optional', None,
                              ({'execution_id':'execution-0001'},), {})
    assert selected_tool_calls(legacy, 'execution-0001') is None


def test_selected_execution_counts_only_its_calls():
    evidence = VerifiedReceipts('a'*64, 'b'*64, 'enforced', None,
        ({'execution_id':'execution-0001'}, {'execution_id':'execution-0002'},
         {'execution_id':'execution-0002'}), {},
        ({'execution_id':'execution-0001'}, {'execution_id':'execution-0002'},
         {'execution_id':'execution-0003'}), True)
    assert selected_tool_calls(evidence, 'execution-0002') == 2
    assert selected_tool_calls(evidence, 'execution-0003') == 0
    assert selected_tool_calls(evidence, 'execution-0004') is None

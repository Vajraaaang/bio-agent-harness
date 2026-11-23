"""Self-contained selector prompts; no provider or scientific computation."""
import json
from pathlib import Path

import pytest

from devbench.analysis.episode_codex import selection_contract, selection_instruction


@pytest.mark.parametrize('context', [
    {'observation': {'allowed_tools': ['audit_expression_quality', 'mean_condition_analysis']}},
    {'decision_kind': 'analysis_proposal', 'allowed_workflows': ['bulk_mean_contrast'],
     'observed': {'condition_levels': ['untrt', 'trt']}},
    {'decision_kind': 'scientific_review', 'research': {'claims': [{'claim_id': 'visible-claim'}]}},
])
def test_nonplanner_selector_can_answer_from_prompt_without_reading_files(context):
    schema, _ = selection_contract(context)
    instruction = selection_instruction(context, schema_path=Path('/owned/actor/answer_schema.json'))
    assert 'Required JSON response schema:\n' in instruction
    inline = instruction.split('Required JSON response schema:\n', 1)[1].split('\n\nTask context:\n', 1)[0]
    assert json.loads(inline) == schema
    assert 'Do not read files, run commands, or call tools' in instruction
    assert 'Every answer is validated locally against that original schema' in instruction


def test_episode_action_inline_schema_preserves_union_and_registered_tool_enum():
    context = {'observation': {'allowed_tools': ['mean_condition_analysis', 'audit_expression_quality']}}
    instruction = selection_instruction(context)
    assert 'Required JSON response schema:\n' in instruction
    inline = instruction.split('Required JSON response schema:\n', 1)[1].split('\n\nTask context:\n', 1)[0]
    schema = json.loads(inline)
    variants = schema['oneOf']
    assert any(v['properties']['kind'].get('const') == 'finish' for v in variants)
    tool = next(v for v in variants if v['properties']['kind'].get('const') == 'tool')
    assert tool['properties']['tool']['enum'] == ['audit_expression_quality', 'mean_condition_analysis']
    assert tool['required'] == ['kind', 'tool', 'decision']
    assert 'parameters are fixed' not in instruction
    assert 'No tool sequence is prescribed' in instruction

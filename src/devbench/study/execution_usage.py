"""Separate repeated work, participant adoption, and selected-execution usage."""


def selected_tool_calls(evidence, execution_id):
    if (evidence is None or not evidence.execution_scoping or execution_id is None
            or not any(row['execution_id'] == execution_id for row in evidence.executions)):
        return None
    return sum(event['execution_id'] == execution_id for event in evidence.events)


def tool_usage_metrics(records):
    attempted = [r for r in records if r.attempted]
    known = [r for r in attempted if r.trusted_tool_capture_complete and r.trusted_tool_calls is not None]
    selected = [r for r in attempted if getattr(r, 'final_execution_tool_calls', None) is not None]
    adopted = sum(r.trusted_tool_calls > 0 for r in known)
    return {
        'tool_adoption_count': adopted,
        'tool_adoption_denominator': len(known),
        'tool_adoption_unknown_count': len(attempted) - len(known),
        'tool_adoption_rate': adopted / len(known) if known else None,
        'tool_adoption_scope': 'participants with at least one coordinator-recorded tool execution; repeated calls do not increase adoption',
        'tool_calls_all_executions_known_subtotal': sum(r.trusted_tool_calls for r in known) if known else None,
        'final_execution_tool_calls_known_subtotal': sum(r.final_execution_tool_calls for r in selected) if selected else None,
        'final_execution_usage_record_count': len(selected),
    }


def validate_execution_usage(record):
    count = record.observed_script_execution_count
    selected = record.final_execution_id
    calls = record.final_execution_tool_calls
    if not record.attempted and any(value is not None for value in (count, selected, calls)):
        raise ValueError('Unattempted records cannot claim execution versions')
    if selected is not None and (count is None or count < 1):
        raise ValueError('Selected execution requires an observed script execution')
    if calls is not None and (selected is None or not record.trusted_tool_capture_complete
            or record.trusted_tool_calls is None or calls > record.trusted_tool_calls):
        raise ValueError('Selected-execution tool use must be bounded by verified total calls')

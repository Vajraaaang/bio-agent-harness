"""Bounded protocol byte-stream checks; no subprocesses or benchmark inputs."""
import io
import json

from devbench_engine.adapters import _OutputCapture, _MAX_EVENT_BYTES


def capture(content):
    events = []
    reader = _OutputCapture(io.BytesIO(content), events.append)
    reader._run()
    return reader, events


def test_dropped_event_is_counted_without_retaining_oversized_bytes():
    reader, events = capture(b'x' * (_MAX_EVENT_BYTES + 1) + b'\n{"type":"done"}\n')
    assert reader.dropped_event_lines == 1
    assert events == [{"type": "done"}]
    assert len(reader.tail) == 0


def test_malformed_and_nonobject_events_have_explicit_counts():
    reader, events = capture(b'not-json\n[]\n{"x":1e999}\n{"type":"done"}\n')
    assert reader.malformed_event_lines == 2
    assert reader.non_object_event_lines == 1
    assert events == [{"type": "done"}]


def test_complete_protocol_objects_have_no_capture_loss():
    reader, events = capture(json.dumps({"type": "turn.completed", "model": "observed-model"}).encode())
    assert len(events) == 1
    assert reader.dropped_event_lines == reader.malformed_event_lines == reader.non_object_event_lines == 0


def test_registered_investigation_uses_one_exact_local_schema_wrapper():
    from devbench_engine import adapters
    from devbench.analysis.episode_codex import selection_instruction
    from devbench.analysis.rna_investigation_contract import investigation_context
    from tests.test_rna_investigation_contract import packet
    assert hasattr(adapters,"local_schema_prompt"), "Native wrapper has no shared pure composer"
    context=investigation_context(packet())
    schema_path="/fixture/actor/answer_schema.json"
    base=selection_instruction(context)
    composed=selection_instruction(context,schema_path=schema_path)
    assert composed==adapters.local_schema_prompt(base,schema_path)
    assert composed.count("Every answer is validated locally")==1
    assert len(composed.encode())>len(base.encode())

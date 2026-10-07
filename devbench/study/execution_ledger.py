"""Coordinator-owned sequential execution windows for tool attribution."""
from contextlib import contextmanager
import re
import threading


class ExecutionLedger:
    def __init__(self):
        self.condition = threading.Condition()
        self.records = []
        self.active = None
        self.pending = 0
        self.error = None
        self.wait_seconds = 2.0

    def _fail(self, message):
        self.error = message
        self.condition.notify_all()
        raise ValueError(message)

    def observe(self, phase, context):
        with self.condition:
            if self.error:
                raise ValueError(self.error)
            identifier = context.get('execution_id')
            if not isinstance(identifier, str) or not re.fullmatch(r'execution-[0-9]{4}', identifier):
                self._fail('Invalid coordinator execution identifier')
            if phase == 'started':
                if self.active is not None:
                    self._fail('Overlapping active script execution windows')
                if len(self.records) >= 128 or any(r['execution_id'] == identifier for r in self.records):
                    self._fail('Duplicate or excessive execution windows')
                if context.get('start_observed') is not True:
                    self._fail('Execution start was not observed')
                self.active = {key: context.get(key) for key in (
                    'execution_id', 'sequence', 'cli_item_id', 'command_sha256', 'start_observed')}
                self.records.append(self.active)
                self.condition.notify_all()
            elif phase == 'completed':
                if self.active is None or self.active['execution_id'] != identifier:
                    self._fail('Completion has no matching active execution')
                if self.pending:
                    self._fail('Execution completed while tool requests remained active')
                if self.active['command_sha256'] != context.get('command_sha256'):
                    self._fail('Execution command changed between start and completion')
                self.active.update({key: context.get(key) for key in (
                    'direct_script_invocation', 'succeeded', 'exit_code',
                    'script_sha256_at_completion', 'answer_sha256_at_completion')})
                self.active['completed'] = True
                self.active = None
            else:
                self._fail('Unknown execution observation phase')

    @contextmanager
    def request_scope(self):
        with self.condition:
            # A child can reach the socket before the stdout reader consumes
            # item.started. Wait briefly instead of inventing an attribution.
            self.condition.wait_for(lambda: self.active is not None or self.error,
                                    timeout=self.wait_seconds)
            if self.error or self.active is None:
                self._fail('Tool request has no observed active script execution')
            identifier = self.active['execution_id']
            self.pending += 1
        try:
            yield identifier
        finally:
            with self.condition:
                self.pending -= 1

    def seal(self):
        with self.condition:
            if self.active is not None or self.pending:
                self.error = self.error or 'Execution window remained incomplete at capture close'
            return [dict(row) for row in self.records], self.error

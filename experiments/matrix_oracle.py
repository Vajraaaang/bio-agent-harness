"""An explicitly labeled oracle used only to validate synthetic grading infrastructure."""
import copy
from pathlib import Path
from typing import Dict

from devbench_engine.adapters import InProcessMockAdapter


class MatrixOracleAdapter(InProcessMockAdapter):
    def __init__(self) -> None:
        self.truth: Dict = {}
        super().__init__(self._answer)

    def set_truth(self, truth: dict) -> None:
        self.truth = copy.deepcopy(truth)

    def _answer(self, instruction: str, workspace: Path) -> dict:
        if "reference_answer" not in self.truth:
            from experiments.reference_agent import reference_agent
            return reference_agent(instruction, workspace)
        return copy.deepcopy(self.truth["reference_answer"])

    def execute(self, instruction: str, workspace_dir: Path) -> dict:
        result = super().execute(instruction, workspace_dir)
        self.metadata.update(oracle=True, billing_mode="synthetic_oracle", inference_performed=False)
        return result

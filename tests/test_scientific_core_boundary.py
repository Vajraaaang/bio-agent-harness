"""Scientific kernels must not import application or benchmark adapters."""

import ast
from pathlib import Path


def test_scientific_core_does_not_import_application_adapters():
    root = Path(__file__).resolve().parents[1]
    core = root / "src" / "devbench" / "core"
    forbidden = (
        "devbench.analysis", "devbench.engine", "devbench.platform", "devbench.study",
        "devharness", "devharness_core", "devharness_server", "devharness_controlled", "devbench_engine",
    )
    violations = []
    for path in sorted(core.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if node.level > 1:
                    package = ["devbench", "core", *path.relative_to(core).parts[:-1]]
                    module = ".".join([*package[:len(package) - node.level + 1], module]).rstrip(".")
                modules = [module, *(f"{module}.{alias.name}" for alias in node.names)]
            else:
                continue
            for module in modules:
                if any(module == prefix or module.startswith(prefix + ".") for prefix in forbidden):
                    violations.append(f"{path.name}:{node.lineno}: {module}")
    assert not violations, "Forbidden scientific-core imports:\n" + "\n".join(violations)


def test_continuous_family_fit_uses_injected_estimator_and_retains_diagnostics():
    import numpy as np
    import pandas as pd
    import statsmodels.api as sm

    from devbench.core.family_fitting import _fit_family
    from devbench.core.observed_data import ObservedData

    class Estimator:
        def fit_gaussian(self, values, design):
            return sm.OLS(values, design).fit()

        def fit_negative_binomial(self, values, design, *, alpha, offset):
            raise AssertionError("Continuous expression must use the Gaussian estimator")

    samples = ("a1", "a2", "b1", "b2")
    design = pd.DataFrame({"intercept": [1, 1, 1, 1], "group": [0, 0, 1, 1]}, index=samples)
    data = ObservedData(np.array([[1.0, 3.0, 4.0, 6.0]]), ("g1",), samples,
                        pd.DataFrame(index=samples), {}, "continuous", {}, (), {})
    diagnostics = {}

    fits, failures = _fit_family(data, design, diagnostics, estimator=Estimator())

    assert not failures
    np.testing.assert_allclose(fits["g1"].params, [2, 3])
    assert diagnostics["design_condition"]["condition_number_validated"]
    assert diagnostics["plot_evidence"]["reason"] == "raw_count_plot_evidence_unavailable_for_continuous_input"

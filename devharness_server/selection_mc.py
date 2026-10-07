"""Bounded Monte Carlo rank inference for a fixed Gaussian model selection rule.

This research kernel assumes independent homoskedastic Gaussian errors and a
common reduced mean space. It repeats the full selection rule under that null;
the resulting rank probability is marginal over selection. It does not qualify
other error models, multiplicity procedures, intervals, or clinical use.
"""

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
from numbers import Integral

import numpy as np
import pandas as pd
from scipy.stats import f
from devharness_core.design_terms import uses_temporal_variable


_MAX_BATCH_ELEMENTS = 2_000_000
_MAX_ROWS = 10_000
_MAX_COLUMNS = 128
_EPSILON = np.finfo(np.float64).eps
_UNIT_ROUNDOFF = _EPSILON / 2


def _gamma(operations):
    """Standard normal-arithmetic accumulation factor k*u/(1-k*u)."""
    return (operations * _UNIT_ROUNDOFF) / (1 - operations * _UNIT_ROUNDOFF)


def _row_norm(values):
    return np.sqrt(np.einsum("dn,dn->d", values, values, optimize=False))


def _transform_residuals(responses, reflectors, rank):
    """Apply compact Q.T and estimate rounding uncertainty in its residual tail.

    For H=I-tau*v*v.T, dot/multiply/subtract errors use gamma_k. Componentwise
    propagation preserves exact zero support; a simultaneous norm bound avoids
    exponential growth from repeatedly applying abs(H). Both describe arithmetic
    with the stored reflectors, not a certified enclosure of the exact QR.
    """
    transformed = responses.copy(order="C")
    component_error = np.zeros_like(transformed)
    norm_error = np.zeros(len(responses))
    for start, vector, tau, dot_gamma, norm_bound in reflectors:
        section = transformed[:, start:]
        error_section = component_error[:, start:]
        absolute_vector = np.abs(vector)
        dot = np.einsum("dn,n->d", section, vector, optimize=False)
        dot_error = dot_gamma * np.einsum(
            "dn,n->d", np.abs(section), absolute_vector, optimize=False
        )
        propagated_dot_error = np.einsum(
            "dn,n->d", error_section, absolute_vector, optimize=False
        )
        correction = (tau * dot)[:, None] * vector
        section -= correction
        # Subtraction by zero is exact. In particular, untouched residual
        # coordinates acquire no artificial uncertainty from fitted coordinates.
        local_error = (
            (np.abs(tau) * dot_error)[:, None] * absolute_vector
            + _gamma(2) * np.abs(correction)
            + (_UNIT_ROUNDOFF / (1 - _UNIT_ROUNDOFF))
            * np.abs(section) * (correction != 0)
        )
        norm_error = norm_bound * norm_error + _row_norm(local_error)
        error_section += (
            (np.abs(tau) * propagated_dot_error)[:, None] * absolute_vector + local_error
        )
        np.minimum(component_error, norm_error[:, None], out=component_error)
    tail = transformed[:, rank:]
    error = np.minimum(norm_error, _row_norm(component_error[:, rank:]))
    return tail, error


def _residual_operator(scaled, smallest_singular):
    """Retain compact reflectors and a measured bound on reduced-space leakage."""
    rank = scaled.shape[1]
    if rank == 0:
        return {"reflectors": (), "rank": 0, "space_error": 0.0}
    raw, taus = np.linalg.qr(scaled, mode="raw")
    reflectors = []
    for start, tau in enumerate(taus):
        if tau == 0:
            continue
        vector = raw[start, start:].copy()
        vector[0] = 1
        vector.setflags(write=False)
        dot_gamma = _gamma(2 * len(vector))
        squared_norm = float(vector @ vector)
        # An exact stored reflector has operator norm max(1, |1-tau*v.T*v|).
        # Inflate its computed value for the dot product's rounding uncertainty.
        norm_bound = max(1.0, abs(1 - tau * squared_norm)) + abs(tau) * dot_gamma * squared_norm
        reflectors.append((start, vector, float(tau), dot_gamma, float(norm_bound)))
    reflectors = tuple(reflectors)
    tail, arithmetic_error = _transform_residuals(scaled.T, reflectors, rank)
    # If y=X*beta, ||tail(Q.T*y)|| <= ||tail(Q.T*X)||*||beta||,
    # with ||beta|| <= ||y||/sigma_min(X). Frobenius norms upper-bound the
    # matrix spectral norm here. The transform's arithmetic diagnostic is added
    # before division; exactly untouched coordinate spaces have zero leakage.
    space_error = float(
        (np.linalg.norm(tail) + np.linalg.norm(arithmetic_error)) / smallest_singular
    )
    if not np.isfinite(space_error):
        raise ValueError("QR residual-space error could not be resolved")
    return {"reflectors": reflectors, "rank": rank, "space_error": space_error}


def _resolved_rss(responses, operator, label):
    residual, arithmetic_error = _transform_residuals(
        responses, operator["reflectors"], operator["rank"]
    )
    rss = np.einsum("dn,dn->d", residual, residual, optimize=False)
    residual_norm = np.sqrt(rss)
    uncertainty = arithmetic_error + operator["space_error"] * _row_norm(responses)
    if (np.any(~np.isfinite(rss)) or np.any(~np.isfinite(uncertainty))
            or np.any(rss / responses.shape[1] <= np.finfo(float).tiny)
            or np.any(residual_norm <= uncertainty)):
        raise ValueError(f"{label} has no usable residual information; variance is nonfinite or unresolved")
    rss_error = (
        (2 * residual_norm + uncertainty) * uncertainty
        + _gamma(2 * residual.shape[1]) * rss
    )
    return rss, rss_error


def _integer(value, name, minimum, maximum):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Integral):
        raise ValueError(f"{name} must be a nonboolean integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return int(value)


def _matrix_hash(matrix, identity):
    """Hash a JSON identity, newline, then C-order little-endian float64 values."""
    header = json.dumps(
        {"shape": list(matrix.shape), "dtype": "<f8", **identity},
        sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
    ).encode("utf-8")
    digest = hashlib.sha256(header + b"\n")
    digest.update(np.asarray(matrix, dtype="<f8", order="C").tobytes(order="C"))
    return digest.hexdigest()


def _thin_basis(matrix, label):
    """Check actual rank/conditioning and retain only an n-by-p orthonormal basis."""
    if matrix.shape[1] == 0:
        return np.empty((matrix.shape[0], 0)), 1.0, _residual_operator(matrix, 1.0)
    scale = np.max(np.abs(matrix))
    if scale == 0:
        raise ValueError(f"{label} is rank deficient")
    scaled = matrix / scale
    try:
        singular = np.linalg.svd(scaled, compute_uv=False)
        tolerance = _EPSILON * max(matrix.shape) * singular[0]
        if singular[-1] <= tolerance:
            raise ValueError(f"{label} is rank deficient")
        condition = float(singular[0] / singular[-1])
        if not np.isfinite(condition) or condition > 1000:
            raise ValueError(f"{label} condition number exceeds 1000")
        basis, _ = np.linalg.qr(scaled, mode="reduced")
        operator = _residual_operator(scaled, float(singular[-1]))
    except np.linalg.LinAlgError as error:
        raise ValueError(f"{label} rank/conditioning could not be resolved") from error
    basis.setflags(write=False)
    return basis, condition, operator


def _assert_contained(inner, outer, label):
    """Test containment with thin products, never an n-by-n projector."""
    if inner.shape[1] == 0:
        return
    remainder = inner - outer @ (outer.T @ inner)
    tolerance = 32 * _EPSILON * max(inner.shape[0], outer.shape[1])
    if np.max(np.linalg.norm(remainder, axis=0)) > tolerance:
        raise ValueError(f"{label}: reduced nuisance space containment failed")


class GaussianSelectionMC:
    """Share one lazy, seeded null reference across informative response vectors.

    ``designs`` maps positive integer df labels to the existing
    ``(formula, Patsy design DataFrame, diagnostics)`` tuples. The diagnostics
    are advisory: finite values, rank, conditioning, temporal columns, and the
    shared reduced space are checked against defensive matrix copies.
    """

    def __init__(self, designs, criterion, draws=65535, seed=0):
        if not isinstance(criterion, str) or criterion not in ("aic", "bic", "gcv"):
            raise ValueError("criterion must be aic, bic, or gcv")
        self._draws = _integer(draws, "draws", 1, 1_000_000)
        self._seed = _integer(seed, "seed", 0, 2 ** 64 - 1)
        self._criterion = criterion
        if not isinstance(designs, Mapping) or not designs:
            raise ValueError("designs must be a nonempty candidate mapping")
        for df in designs:
            _integer(df, "candidate df", 1, 2 ** 63 - 1)
        self._dfs = tuple(int(df) for df in sorted(designs))
        self._operators = []
        self._ranks = []
        self._temporal_counts = []
        self._dimensions = {}
        self._matrix_hashes = {}
        common_null_basis = None
        self._reference_probabilities = None
        self._reference_hash = None
        self._reference_status = "not_run"
        self._attempted_draws = 0
        self._evaluated_draws = 0
        self._reference_error = None
        row_index = None

        for df in self._dfs:
            entry = designs[df]
            if not isinstance(entry, (tuple, list)) or len(entry) != 3:
                raise ValueError("Each candidate design must be a formula/frame/diagnostics tuple")
            formula, frame, _ = entry
            if not isinstance(formula, str) or not formula:
                raise ValueError("Each candidate design formula must be a nonempty string")
            if not isinstance(frame, pd.DataFrame) or not hasattr(frame, "design_info"):
                raise ValueError("Each candidate requires a Patsy DataFrame with design_info")
            n, p = frame.shape
            if not 1 <= n <= _MAX_ROWS:
                raise ValueError(f"Candidate designs require 1 to {_MAX_ROWS} rows")
            if not 1 <= p <= _MAX_COLUMNS:
                raise ValueError(f"Candidate designs require 1 to {_MAX_COLUMNS} columns")
            if n <= p:
                raise ValueError("Candidate designs require positive residual degrees of freedom")
            if n * len(self._dfs) > _MAX_BATCH_ELEMENTS:
                raise ValueError("Candidate observation dimensions exceed the bounded batch budget")
            if row_index is None:
                row_index = frame.index.copy(deep=True)
                self._n = n
            elif not row_index.equals(frame.index):
                raise ValueError("Candidate design rows must have identical ordered index alignment")
            raw = frame.to_numpy()
            if raw.dtype.kind not in "iuf" or not np.isfinite(raw).all():
                raise ValueError("Candidate design matrices must contain finite real numeric values")
            with np.errstate(over="ignore", invalid="ignore"):
                matrix = np.array(raw, dtype=np.float64, order="C", copy=True)
            if not np.isfinite(matrix).all():
                raise ValueError("Candidate design values must be finite in float64")
            columns = [str(column) for column in frame.columns]
            info = frame.design_info
            if list(getattr(info, "column_names", ())) != list(frame.columns):
                raise ValueError("Patsy design_info columns do not match the candidate matrix")
            slices = getattr(info, "term_name_slices", None)
            if not isinstance(slices, Mapping):
                raise ValueError("Patsy design_info requires term_name_slices")
            temporal, covered, terms = [], [], {}
            for term, sl in slices.items():
                if (not isinstance(term, str) or not isinstance(sl, slice)
                        or not isinstance(sl.start, Integral) or not isinstance(sl.stop, Integral)
                        or sl.step not in (None, 1) or not 0 <= sl.start <= sl.stop <= p):
                    raise ValueError("Patsy design_info contains invalid term slices")
                indices = list(range(sl.start, sl.stop))
                covered.extend(indices)
                terms[term] = [int(sl.start), int(sl.stop)]
                if uses_temporal_variable(term):
                    temporal.extend(indices)
            if sorted(covered) != list(range(p)):
                raise ValueError("Patsy term slices must cover every design column exactly once")
            if not temporal:
                raise ValueError("Each candidate requires temporal terms containing numeric_time")
            temporal = sorted(temporal)
            retained = [i for i in range(p) if i not in temporal]
            reduced = matrix[:, retained]
            basis, condition, operator = _thin_basis(matrix, f"Candidate df={df}")
            null_basis, _, null_operator = _thin_basis(reduced, f"Reduced candidate df={df}")
            _assert_contained(null_basis, basis, f"Candidate df={df}")
            row_names = [str(value) for value in row_index]
            if common_null_basis is None:
                common_null_basis = null_basis
                self._null_operator = null_operator
                self._reduced_hash = _matrix_hash(reduced, {
                    "columns": [columns[i] for i in retained], "rows": row_names,
                })
            else:
                if null_basis.shape[1] != common_null_basis.shape[1]:
                    raise ValueError("Candidates do not share the same reduced nuisance space")
                _assert_contained(null_basis, common_null_basis, "Common reduced nuisance space")
                _assert_contained(common_null_basis, null_basis, "Common reduced nuisance space")
            self._operators.append(operator)
            self._ranks.append(p)
            self._temporal_counts.append(len(temporal))
            self._dimensions[df] = {
                "full_columns": p, "reduced_columns": len(retained),
                "temporal_columns": len(temporal), "residual_df": n - p,
                "condition_number": condition,
                "qr_residual_space_error": operator["space_error"],
            }
            self._matrix_hashes[df] = _matrix_hash(matrix, {
                "df": df, "formula": formula, "columns": columns, "rows": row_names,
                "term_slices": terms, "temporal_columns": temporal,
            })

        self._operators = tuple(self._operators)
        self._ranks = tuple(self._ranks)
        self._temporal_counts = tuple(self._temporal_counts)
        self._chunk_size = min(1024, self._draws, _MAX_BATCH_ELEMENTS // (self._n * len(self._dfs)))

    def _evaluate_batch(self, responses):
        """Evaluate draw-major responses through the same arithmetic for every use.

        Explicit, unoptimized contractions retain the same summation direction
        for singleton observed responses and multirow simulation blocks. No
        sample-by-sample projectors or sample-by-draw-by-candidate tensors exist.
        """
        if np.any(np.all(responses == responses[:, :1], axis=1)):
            raise ValueError("Constant response has no information; residual variance is unresolved")
        size = len(responses)
        scores = {name: np.empty((len(self._dfs), size))
                  for name in ("aic", "bic", "gcv", "log_likelihood")}
        statistics = np.empty((len(self._dfs), size))
        probabilities = np.empty_like(statistics)
        with np.errstate(over="ignore", invalid="ignore", divide="ignore", under="ignore"):
            rss0, rss0_error = _resolved_rss(responses, self._null_operator, "Reduced model")
            for index, (operator, rank, temporal_count) in enumerate(
                zip(self._operators, self._ranks, self._temporal_counts)
            ):
                rss, rss_error = _resolved_rss(responses, operator, f"Candidate df={self._dfs[index]}")
                variance = rss / self._n
                improvement = rss0 - rss
                roundoff = rss0_error + rss_error + _gamma(1) * np.abs(improvement)
                if np.any(improvement < -roundoff):
                    raise ValueError("Nested-model RSS improvement exceeds numerical roundoff tolerance")
                statistic = (np.maximum(improvement, 0) / temporal_count) / (rss / (self._n - rank))
                probability = f.sf(statistic, temporal_count, self._n - rank)
                minus2ll = self._n * (np.log(2 * np.pi) + 1 + np.log(variance))
                scores["log_likelihood"][index] = -minus2ll / 2
                scores["aic"][index] = minus2ll + 2 * (rank + 1)
                scores["bic"][index] = minus2ll + np.log(self._n) * (rank + 1)
                scores["gcv"][index] = (rss / (self._n - rank)) * (self._n / (self._n - rank))
                statistics[index] = statistic
                probabilities[index] = probability
        if (not all(np.isfinite(values).all() for values in scores.values())
                or not np.isfinite(statistics).all() or not np.isfinite(probabilities).all()):
            raise ValueError("Nonfinite Gaussian selection score or F statistic")
        # Candidate rows are sorted by df, so argmin implements score-then-df ties.
        selected = np.argmin(scores[self._criterion], axis=0)
        columns = np.arange(size)
        return {
            "scores": scores, "selected_indices": selected,
            "conditional_p_values": probabilities[selected, columns],
            "conditional_f_statistics": statistics[selected, columns],
        }

    def _ensure_reference(self):
        if self._reference_probabilities is not None:
            return
        if self._reference_status == "failed":
            raise ValueError(f"Reference generation previously failed: {self._reference_error}")
        try:
            self._reference_status = "running"
            rng = np.random.Generator(np.random.PCG64(self._seed))
            reference = np.empty(self._draws, dtype=np.float64)
            for start in range(0, self._draws, self._chunk_size):
                stop = min(start + self._chunk_size, self._draws)
                # Draw-major layout preserves the stream and row grouping when
                # the effective chunk size changes. No failed draw is replaced.
                responses = rng.standard_normal((stop - start, self._n))
                self._attempted_draws += stop - start
                result = self._evaluate_batch(responses)
                reference[start:stop] = result["conditional_p_values"]
                self._evaluated_draws += stop - start
            reference_hash = hashlib.sha256(
                np.asarray(reference, dtype="<f8").tobytes(order="C")
            ).hexdigest()
            reference.sort()
            reference.setflags(write=False)
            # Publication is part of the attempt: an interruption in any of
            # these assignments must also clear both retained reference fields.
            self._reference_hash = reference_hash
            self._reference_probabilities = reference
            self._reference_status = "completed"
        except BaseException as error:
            self._reference_probabilities = None
            self._reference_hash = None
            self._reference_status = "failed"
            self._reference_error = f"{type(error).__name__}: {error}"
            raise

    def evaluate(self, y):
        """Return selected-model diagnostics and the inclusive Monte Carlo rank."""
        raw = np.asarray(y)
        if raw.ndim != 1 or len(raw) != self._n:
            raise ValueError(f"Response must be one vector of length {self._n}")
        if raw.dtype.kind not in "iuf" or not np.isfinite(raw).all():
            raise ValueError("Response vector must contain finite real numeric values")
        with np.errstate(over="ignore", invalid="ignore"):
            response = np.array(raw, dtype=np.float64, order="C", copy=True)
        if not np.isfinite(response).all():
            raise ValueError("Response values must be finite in float64")
        result = self._evaluate_batch(response[None, :])
        selected = int(result["selected_indices"][0])
        conditional_p = float(result["conditional_p_values"][0])
        candidate_scores = {
            df: {**{name: float(values[index, 0]) for name, values in result["scores"].items()},
                 "num_parameters": self._ranks[index] + 1}
            for index, df in enumerate(self._dfs)
        }
        self._ensure_reference()
        count = int(np.searchsorted(self._reference_probabilities, conditional_p, side="right"))
        return {
            "selected_df": self._dfs[selected], "candidate_scores": candidate_scores,
            "conditional_p_value": conditional_p,
            "conditional_f_statistic": float(result["conditional_f_statistics"][0]),
            "conditional_df_num": self._temporal_counts[selected],
            "conditional_df_denom": self._n - self._ranks[selected],
            "p_value": float((1 + count) / (self._draws + 1)),
        }

    def metadata(self):
        """Describe the fixed method and only completed reference generation."""
        completed = self._reference_status == "completed"
        return {
            "schema_version": "gaussian_selection_mc.v1",
            "method": "full_selection_gaussian_monte_carlo_rank",
            "status": self._reference_status,
            "criterion": self._criterion,
            "requested_draws": self._draws, "completed_draws": self._evaluated_draws,
            "attempted_draws": self._attempted_draws, "evaluated_draws": self._evaluated_draws,
            "reference_error": self._reference_error,
            "draw_count_semantics": {
                "attempted_draws": "Generated rows submitted to the batch evaluator.",
                "evaluated_draws": "Rows in batches completing every candidate and full selection successfully.",
                "completed_draws": "Alias of evaluated_draws; a reference is usable only when status is completed.",
            },
            "seed": self._seed, "generator": "PCG64", "simulation_layout": "draw_major",
            "reference_distribution": "standard_normal",
            "effective_chunk_size": self._chunk_size,
            "num_observations": self._n, "num_candidates": len(self._dfs),
            "candidate_dimensions": {str(df): deepcopy(record) for df, record in self._dimensions.items()},
            "candidate_matrix_hashes": {str(df): digest for df, digest in self._matrix_hashes.items()},
            "reduced_matrix_hash": self._reduced_hash,
            "reference_probability_hash": self._reference_hash if completed else None,
            "matrix_hash_encoding": "sha256(sorted compact JSON identity + newline + C-order little-endian float64)",
            "reference_hash_encoding": "sha256(generation-order little-endian float64 probabilities)",
            "p_value_resolution": 1 / (self._draws + 1),
            "rank_rule": "(1 + count(reference_conditional_p <= observed_conditional_p)) / (draws + 1)",
            "numerical_kernel_version": "householder_roundoff_v2",
            "temporal_term_interpretation": "parsed_variable_identity/1",
            "numerical_resolution_policy": (
                "Compact Householder residual tails must exceed propagated arithmetic and QR space-leakage "
                "diagnostics; residual variance must be positive normal float64. The gamma_k rounding model "
                "gives a practical conservative diagnostic, not a certified error enclosure or formal guarantee."
            ),
            "reduced_qr_residual_space_error": self._null_operator["space_error"],
            "assumptions": [
                "Independent homoskedastic Gaussian errors with mean in the common reduced nuisance space under the null.",
                "Fixed design matrices, candidate set, criterion, and simulation seed, specified independently of tested responses.",
                "Inference is marginal over the full selection rule; conditional diagnostics describe only the selected model.",
                "The simulated reference is independent of the observed response and shared across evaluations; resulting gene probabilities can be dependent.",
                "The requested gene family must be prespecified; this kernel performs no multiplicity adjustment and claims no FDR control.",
                "Residual variance must be finite and numerically resolved for every candidate; no failed candidate or null draw is silently replaced.",
                "A failed reference is terminal for this object; partial probabilities are unavailable and failed simulation is not implicitly retried.",
                "No clinical, heteroskedastic, count, correlated-error, or interval qualification is provided.",
            ],
        }

"""Independent properties of the frozen coverage generator, not a clinical grade."""
import math
import numpy as np
from patsy import dmatrix
import pytest
from scipy.stats import beta
from scripts.native_effect_coverage import curves, binomial_interval


@pytest.mark.parametrize("n",[24,48])
def test_generating_curves_are_in_model_space_and_lock_endpoint_truth(n):
    times=np.repeat(np.arange(6,dtype=float),n//6)
    full=np.column_stack((np.ones(n),np.asarray(dmatrix("cr(t, df=3, constraints='center') - 1",{'t':times})),np.tile([0,1],n//2)))
    contrast=full[5*(n//6),1:4]-full[0,1:4]
    positive,negative,neutral,nonlinear=curves(full,contrast)
    assert full[:,1:4]@positive == pytest.approx((times-times.mean())/5*math.log(4),abs=1e-12)
    assert contrast@positive/math.log(2) == pytest.approx(2,abs=1e-12)
    assert contrast@negative/math.log(2) == pytest.approx(-2,abs=1e-12)
    assert abs(contrast@neutral) < 1e-12 and np.ptp(full[:,1:4]@neutral) == pytest.approx(math.log(4))
    assert contrast@nonlinear/math.log(2) == pytest.approx(2,abs=1e-12)
    assert np.linalg.matrix_rank(full) == 5


def test_extreme_coverage_does_not_get_a_false_zero_width_interval():
    assert binomial_interval(0,20)[1] == pytest.approx(1-.025**(1/20))
    assert binomial_interval(20,20)[0] == pytest.approx(.025**(1/20))
    assert binomial_interval(0,0) == [None,None]
    assert binomial_interval(90,100) == pytest.approx([beta.ppf(.025,90,11),beta.ppf(.975,91,10)])


def test_final_report_seal_includes_all_redirected_output(tmp_path, monkeypatch):
    """An empty infrastructure schedule exercises finalization, not statistics."""
    import contextlib
    import hashlib
    import json
    from scripts import native_effect_coverage as coverage
    frozen = tmp_path / "frozen"
    frozen.mkdir()
    (frozen / "protocol.json").write_text(json.dumps({"concurrency": 1, "cells": [], "scope": "test only"}))
    (frozen / "schedule.json").write_text("[]")
    monkeypatch.setattr(coverage, "verify", lambda root: None)
    with (tmp_path / "run.log").open("w") as output, contextlib.redirect_stdout(output):
        coverage.run(tmp_path)
    seal = json.loads((tmp_path / "SEAL.json").read_bytes())
    assert all(hashlib.sha256((tmp_path / name).read_bytes()).hexdigest() == digest
               for name, digest in seal["files"].items())

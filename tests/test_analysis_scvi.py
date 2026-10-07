import json

import numpy as np
import pytest

pytest.importorskip("scvi")


def test_scvi_trains_on_cpu_and_resumes_real_optimizer_checkpoint(tmp_path):
    from lightning.pytorch.callbacks import Callback
    from tests.test_analysis_singlecell import counts_fixture
    from devbench.analysis.embedding import train_scvi

    class Interrupt(Callback):
        def on_train_epoch_end(self, trainer, pl_module):
            raise RuntimeError("fixture_disconnect")
    data = counts_fixture()
    data.layers["counts"] = data.X.copy()
    folder = tmp_path/"scvi"
    with pytest.raises(RuntimeError, match="fixture_disconnect"):
        train_scvi(data.copy(), folder, batch_key="batch", seed=19, max_epochs=3,
                   n_latent=4, identity_sha256="a"*64, extra_callbacks=[Interrupt()])
    state = json.loads((folder/"training-state.json").read_text())
    assert state["global_step"] > 0 and state["epoch"] == 0
    report = train_scvi(data.copy(), folder, batch_key="batch", seed=19, max_epochs=3,
                        n_latent=4, identity_sha256="a"*64)
    assert report["resumed_from_epoch"] == 0
    assert report["epochs_completed"] == 3
    assert np.load(folder/"latent.npy").shape == (80, 4)
    assert np.isfinite(np.load(folder/"latent.npy")).all()
    assert (folder/"model/model.pt").is_file()
    with pytest.raises(ValueError, match="checkpoint_identity_mismatch"):
        train_scvi(data.copy(), folder, batch_key="batch", seed=19, max_epochs=3,
                   n_latent=4, identity_sha256="b"*64)

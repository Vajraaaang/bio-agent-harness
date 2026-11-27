FROM python@sha256:cc55e4ded9c95f6ce2133b3d096db43f214d280c2a8af65d83e246c8fa7d168c AS python-packages
COPY containers/rna-kernel-2.0/python-build.lock containers/rna-kernel-2.0/python-runtime.lock /tmp/locks/
# The pinned wheel build tools are needed for asciitree and the arm64 numcodecs sdist.
RUN python -m pip wheel --no-deps --require-hashes --wheel-dir /tmp/build-wheels -r /tmp/locks/python-build.lock \
 && python -m pip install --no-index --no-deps --require-hashes --find-links=/tmp/build-wheels -r /tmp/locks/python-build.lock \
 && python -m pip download --no-deps --no-build-isolation --require-hashes --dest /tmp/runtime-sources -r /tmp/locks/python-runtime.lock
RUN --network=none python -m pip install --no-index --no-deps --no-build-isolation --require-hashes \
    --find-links=/tmp/runtime-sources --target=/opt/rna/python -r /tmp/locks/python-runtime.lock

FROM python-packages AS python-scrna-packages
COPY containers/rna-kernel-2.0/python-scrna.lock containers/rna-kernel-2.0/python-torch-cpu.lock /tmp/locks/
RUN python -m pip download --no-deps --no-build-isolation --require-hashes \
    --dest /tmp/scrna-sources -r /tmp/locks/python-scrna.lock \
 && python -m pip download --no-deps --only-binary=:all: --require-hashes \
    --index-url=https://download.pytorch.org/whl/cpu \
    --dest /tmp/scrna-sources -r /tmp/locks/python-torch-cpu.lock
# Keep Annoy's upstream flags without its build-host-specific -march=native.
RUN --network=none ANNOY_COMPILER_ARGS=-D_CRT_SECURE_NO_WARNINGS,-fpermissive,-O3,-ffast-math,-fno-associative-math,-DANNOYLIB_MULTITHREADED_BUILD,-std=c++14 \
    python -m pip --no-cache-dir install --no-index --no-deps --no-build-isolation --require-hashes \
    --find-links=/tmp/scrna-sources --target=/opt/rna/scrna-python \
    -r /tmp/locks/python-scrna.lock -r /tmp/locks/python-torch-cpu.lock

FROM python-packages AS python-cellbender-packages
COPY containers/rna-kernel-2.0/python-cellbender.lock /tmp/locks/
RUN python -m pip download --no-deps --only-binary=:all: --require-hashes \
    --dest /tmp/cellbender-sources -r /tmp/locks/python-cellbender.lock
RUN --network=none python -m pip --no-cache-dir install --no-index --no-deps \
    --only-binary=:all: --require-hashes --find-links=/tmp/cellbender-sources \
    --target=/opt/rna/cellbender-python -r /tmp/locks/python-cellbender.lock

FROM bioconductor/bioconductor_docker@sha256:821dbf9ac119eac41f177531c7ca8fc7084c99c04eb218a1aa7f52cb63bad5d9 AS r-packages
ENV MAKEFLAGS=-j2
COPY containers/rna-kernel-2.0/r-sources.lock.json containers/rna-kernel-2.0/install-packages.R /tmp/
RUN Rscript --vanilla /tmp/install-packages.R fetch
RUN --network=none Rscript --vanilla /tmp/install-packages.R install

FROM r-packages AS r-scrna-packages
COPY containers/rna-kernel-2.0/r-scrna-sources.lock.json containers/rna-kernel-2.0/install-scrna-packages.R /tmp/
RUN Rscript --vanilla /tmp/install-scrna-packages.R fetch
RUN --network=none Rscript --vanilla /tmp/install-scrna-packages.R install

FROM bioconductor/bioconductor_docker@sha256:821dbf9ac119eac41f177531c7ca8fc7084c99c04eb218a1aa7f52cb63bad5d9 AS bulk-runtime
LABEL org.opencontainers.image.title="devbench-rna-kernel" \
      org.opencontainers.image.version="2.0"
ENV R_LIBS_USER=/opt/rna/R-library \
    PYTHONPATH=/opt/rna/python:/opt/rna/runtime \
    PYTHONDONTWRITEBYTECODE=1 \
    OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
COPY --from=python-packages /usr/local/bin/python3.13 /usr/local/bin/python3.13
COPY --from=python-packages /usr/local/lib/libpython3.13.so.1.0 /usr/local/lib/libpython3.13.so.1.0
COPY --from=python-packages /usr/local/lib/python3.13 /usr/local/lib/python3.13
COPY --from=python-packages /opt/rna/python /opt/rna/python
COPY --from=r-packages /opt/rna/R-library /opt/rna/R-library
COPY containers/rna-kernel-2.0/r-sources.lock.json /opt/rna/r-sources.lock.json
COPY src/rna_kernels/__init__.py src/rna_kernels/canonical.py src/rna_kernels/bulk_solver_policy.py \
    src/rna_kernels/mounted_bulk.py src/rna_kernels/mounted_count.py src/rna_kernels/mounted_rank.py src/rna_kernels/mounted_model.py \
    src/rna_kernels/rank_sensitivity.py src/rna_kernels/streaming_rle.py \
    /opt/rna/runtime/rna_kernels/
COPY src/devharness_core/__init__.py src/devharness_core/provenance.py src/devharness_core/storage.py \
    /opt/rna/runtime/devharness_core/
COPY src/rna_kernels/r/mounted_edger.R src/rna_kernels/r/mounted_general.R src/rna_kernels/r/bulk_edger.R \
    src/rna_kernels/r/bulk_deseq2_sensitivity.R src/rna_kernels/r/bulk_dream.R \
    src/rna_kernels/r/bulk_latent_diagnostics.R /opt/rna/runtime/rna_kernels/r/
COPY src/rna_kernels/r/mounted_edger.R src/rna_kernels/r/mounted_general.R src/rna_kernels/r/bulk_edger.R \
    src/rna_kernels/r/bulk_deseq2_sensitivity.R src/rna_kernels/r/bulk_dream.R \
    src/rna_kernels/r/bulk_latent_diagnostics.R /opt/rna/runtime/devharness_server/r/
RUN --network=none ldconfig \
 && python3.13 -c 'from devharness_core.storage import ChunkedExpressionStore; import numpy, scipy, pyarrow; assert numpy.__version__ and scipy.__version__ and pyarrow.__version__' \
 && python3.13 -m rna_kernels.mounted_bulk --help > /dev/null
USER 65532:65532
WORKDIR /tmp
ENTRYPOINT ["python3.13", "-m", "rna_kernels.mounted_bulk"]

# The optional single-cell target extends the bulk kernel with the locked
# CPU analysis environment. The default target keeps the bulk layer alone.
FROM bulk-runtime AS scrna
COPY --from=python-scrna-packages /opt/rna/scrna-python /opt/rna/scrna-python
COPY --from=python-cellbender-packages /opt/rna/cellbender-python /opt/rna/cellbender-python
COPY --from=r-scrna-packages /opt/rna/scrna-R-library /opt/rna/scrna-R-library
COPY containers/rna-kernel-2.0/r-scrna-sources.lock.json containers/rna-kernel-2.0/python-scrna.lock \
    containers/rna-kernel-2.0/python-torch-cpu.lock containers/rna-kernel-2.0/python-cellbender.lock /opt/rna/
ENV PYTHONPATH=/opt/rna/cellbender-python:/opt/rna/scrna-python:/opt/rna/python:/opt/rna/runtime \
    R_LIBS_USER=/opt/rna/scrna-R-library:/opt/rna/R-library \
    NUMBA_CACHE_DIR=/tmp/devbench-numba-cache MPLCONFIGDIR=/tmp/devbench-matplotlib
RUN --network=none --mount=type=tmpfs,target=/tmp python3.13 -m pip --no-cache-dir check \
 && python3.13 -c 'import anndata, harmonypy, jax, psutil, scanpy, scib_metrics, scrublet, scvi, torch; assert torch.version.cuda is None and jax.default_backend() == "cpu"' \
 && python3.13 -m cellbender.base_cli --version \
 && Rscript --vanilla -e 'stopifnot(requireNamespace("DropletUtils", quietly=TRUE), requireNamespace("scran", quietly=TRUE))'
COPY src/rna_kernels/mounted_scrna.py src/rna_kernels/scrna_upstream_contract.py \
    src/rna_kernels/embedding_qc.py src/rna_kernels/scrna_droplets.py \
    src/rna_kernels/scrna_scran.py /opt/rna/runtime/rna_kernels/
COPY src/devbench/__init__.py /opt/rna/runtime/devbench/
COPY src/devbench/core/__init__.py src/devbench/core/_files.py src/devbench/core/_hashing.py \
    src/devbench/core/expression_io.py src/devbench/core/tenx.py /opt/rna/runtime/devbench/core/
COPY src/devbench/analysis/__init__.py src/devbench/analysis/annotation.py \
    src/devbench/analysis/checkpoints.py src/devbench/analysis/embedding.py \
    src/devbench/analysis/integration_metrics.py src/devbench/analysis/qc_concordance.py \
    src/devbench/analysis/resources.py src/devbench/analysis/scrna_qc.py \
    src/devbench/analysis/singlecell.py src/devbench/analysis/streaming.py /opt/rna/runtime/devbench/analysis/
COPY src/rna_kernels/r/scrna_emptydrops.R src/rna_kernels/r/scrna_scran.R \
    /opt/rna/runtime/rna_kernels/r/
COPY src/rna_kernels/r/scrna_emptydrops.R src/rna_kernels/r/scrna_scran.R \
    /opt/rna/runtime/devharness_server/r/
RUN --network=none python3.13 -m rna_kernels.mounted_scrna --help > /dev/null

FROM bulk-runtime AS runtime

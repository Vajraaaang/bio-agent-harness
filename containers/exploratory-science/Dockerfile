# Build Python from the reviewed local scientific base. The named
# "packages" context contains the operator-staged, architecture-specific lock
# and archives. Configure the resulting image ID in the release catalog.
ARG KERNEL_IMAGE=devbench-rna-kernel:2.0-p31-scrna-r3-arm64
FROM ${KERNEL_IMAGE} AS devharness-explore-py
USER 0:0
ENV PYTHONPATH=/opt/exploratory/python:${PYTHONPATH}
RUN --network=none --mount=type=bind,from=packages,source=/,target=/packages,ro \
    python3.13 -m pip install --no-index --no-deps --require-hashes \
      --find-links=/packages/wheels --target=/opt/exploratory/python \
      -r /packages/requirements.lock \
 && python3.13 -P -s -B -c 'import numpy, scipy, pandas, pyarrow, polars, anndata, scanpy, mudata, spatialdata, tiledbsoma, statsmodels, sklearn, matplotlib, seaborn, plotly, umap, igraph, leidenalg, harmonypy'
LABEL org.opencontainers.image.title="devharness-explore-py" \
      org.opencontainers.image.version="1"
USER 65532:65532

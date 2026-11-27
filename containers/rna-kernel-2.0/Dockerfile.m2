# Build with --network=none --build-context r_sources=<staged lock archives>.
ARG KERNEL_IMAGE=devharness-m1-3-streaming:local
# M2_R_IMAGE can name an already verified build of the m2-r stage.
ARG M2_R_IMAGE=m2-r
FROM ${KERNEL_IMAGE} AS m2-r
USER root
COPY containers/rna-kernel-2.0/r-m2-qc-sources.lock.json containers/rna-kernel-2.0/install-m2-qc-packages.R /opt/rna/
RUN --mount=type=bind,from=r_sources,target=/mnt/sources,readonly \
    Rscript --vanilla /opt/rna/install-m2-qc-packages.R /opt/rna/r-m2-qc-sources.lock.json /mnt/sources /opt/rna/m2-R-library
ENV R_LIBS_USER=/opt/rna/m2-R-library:${R_LIBS_USER}
USER 65532:65532

FROM ${M2_R_IMAGE} AS m2
COPY containers/rna-kernel-2.0/verify-m2-qc-sources.R containers/rna-kernel-2.0/install-m2-qc-packages.R containers/rna-kernel-2.0/r-m2-qc-sources.lock.json /opt/rna/
RUN --mount=type=bind,from=r_sources,target=/mnt/sources,readonly \
    Rscript --vanilla /opt/rna/verify-m2-qc-sources.R /opt/rna/r-m2-qc-sources.lock.json /mnt/sources
COPY src/devbench/core/ /opt/rna/runtime/devbench/core/
COPY src/devbench/analysis/singlecell.py src/devbench/analysis/annotation.py /opt/rna/runtime/devbench/analysis/
COPY src/rna_kernels/ /opt/rna/runtime/rna_kernels/

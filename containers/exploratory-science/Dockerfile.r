# The caller verifies the local base image's config ID against the package lock.
# "packages" is a separate, operator-staged named context.
ARG KERNEL_IMAGE=devbench-rna-kernel:2.0-thesis-routed-arm64
FROM ${KERNEL_IMAGE}
ARG TARGETARCH
USER 0:0
LABEL org.opencontainers.image.title="devharness-explore-r" \
      org.opencontainers.image.version="1" \
      org.opencontainers.image.description="Offline exploratory R and Bioconductor runtime"
ENV R_LIBS_USER=/opt/exploratory/R:/opt/rna/pathway-R-library:/opt/rna/scrna-R-library:/opt/rna/R-library \
    VECLIB_MAXIMUM_THREADS=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
COPY r-science-${TARGETARCH}.lock.json /opt/exploratory/r-package-lock.json
COPY install-r-science.R verify-r-science.R /opt/exploratory/
RUN --network=none --mount=type=bind,from=packages,source=/,target=/packages,ro \
    mkdir -p /opt/exploratory/R \
 && Rscript --vanilla /opt/exploratory/install-r-science.R \
 && Rscript --vanilla /opt/exploratory/verify-r-science.R
USER 65532:65532

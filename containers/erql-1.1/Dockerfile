FROM bioconductor/bioconductor_docker@sha256:979991813693d4256f685a565cd59560d7bca7c4d4d41f66ed5fc38b31338594
ARG SOURCE_LOCK_SHA256
LABEL org.devbench.procedure-profile="ERQL-1.1" \
      org.devbench.source-lock-sha256="${SOURCE_LOCK_SHA256}"
ENV R_LIBS_USER=/opt/devharness-erql/R-library \
    OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
    MAKEFLAGS=-j2
COPY package-sources.lock.json /opt/devharness-erql/source-lock.json
COPY sources /opt/devharness-erql/sources
COPY install-packages.R reference-smoke.R /opt/devharness-erql/
RUN --network=none test -n "${SOURCE_LOCK_SHA256}" && \
    printf '%s  %s\n' "${SOURCE_LOCK_SHA256}" /opt/devharness-erql/source-lock.json | sha256sum -c - && \
    Rscript /opt/devharness-erql/install-packages.R
USER 65532:65532
WORKDIR /tmp
ENTRYPOINT ["Rscript"]

FROM bioconductor/bioconductor_docker@sha256:979991813693d4256f685a565cd59560d7bca7c4d4d41f66ed5fc38b31338594
ENV R_LIBS_USER=/opt/devharness-native/R-library \
    OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
    MAKEFLAGS=-j2
COPY package-sources.lock.json /opt/devharness-native/source-lock.json
COPY sources /opt/devharness-native/sources
COPY install-packages.R /opt/devharness-native/install-packages.R
RUN --network=none Rscript /opt/devharness-native/install-packages.R \
    /opt/devharness-native/source-lock.json /opt/devharness-native/sources /opt/devharness-native/R-library
USER 65532:65532
WORKDIR /tmp
ENTRYPOINT ["Rscript"]

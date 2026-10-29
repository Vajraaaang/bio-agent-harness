# Public software only; prepare_context.py creates the narrow build context.
FROM debian:bookworm-slim@sha256:7ccb8b5b60c7654f5b795d94ee7d751cd0811500803f0b09cefcd555e755a55a AS software
COPY --chmod=755 micromamba /usr/local/bin/micromamba
COPY packages /packages
COPY environment-explicit.txt /build/environment-explicit.txt
RUN MAMBA_ROOT_PREFIX=/build/mamba /usr/local/bin/micromamba --no-rc --no-env create --offline -y -p /opt/community -f /build/environment-explicit.txt

FROM debian:bookworm-slim@sha256:7ccb8b5b60c7654f5b795d94ee7d751cd0811500803f0b09cefcd555e755a55a
COPY --from=software /opt/community /opt/community
ENV PATH=/opt/community/bin:/usr/local/bin:/usr/bin:/bin JAVA_HOME=/opt/community/lib/jvm NXF_HOME=/opt/nextflow NXF_PLUGINS_DIR=/opt/nextflow/plugins NXF_OFFLINE=true NXF_DISABLE_CHECK_LATEST=true CAPSULE_CACHE_DIR=/opt/nextflow/capsule PYTHONDONTWRITEBYTECODE=1
COPY upstream.tar.gz /build/upstream.tar.gz
COPY nf-schema-2.5.1.zip /build/plugin.zip
COPY nextflow-25.10.4-dist /opt/nextflow/nextflow-25.10.4-dist
COPY nextflow-launcher /usr/local/bin/nextflow
COPY collect_inventory.py /opt/devbench/collect_inventory.py
COPY software_preflight.py /opt/devbench/software_preflight.py
COPY package-lock.linux-arm64.json /opt/devbench/package-lock.json
RUN mkdir -p /opt/nf-core/differentialabundance /opt/nextflow/plugins/nf-schema-2.5.1 /opt/nf-core/configs && \
    tar -xzf /build/upstream.tar.gz --strip-components=1 -C /opt/nf-core/differentialabundance && \
    unzip -q /build/plugin.zip -d /opt/nextflow/plugins/nf-schema-2.5.1 && \
    touch /opt/nf-core/configs/nfcore_custom.config /opt/nf-core/configs/pipeline-empty.config && \
    mkdir /opt/nf-core/configs/pipeline && touch /opt/nf-core/configs/pipeline/differentialabundance.config && \
    chmod 755 /usr/local/bin/nextflow /opt/nextflow/nextflow-25.10.4-dist && \
    for program in /opt/community/bin/*; do test -e /usr/local/bin/"$(basename "$program")" || ln -s "$program" /usr/local/bin/"$(basename "$program")"; done && \
    nextflow -version && \
    Rscript -e 'for (x in c("DESeq2","BiocParallel","limma","shinyngs")) {library(x,character.only=TRUE); cat(x,as.character(packageVersion(x)),"\n")}' && \
    python3 /opt/devbench/software_preflight.py && \
    python3 /opt/devbench/collect_inventory.py && \
    rm -rf /build && chmod -R a-w /opt
USER 65532:65532
WORKDIR /tmp

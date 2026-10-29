# Offline community RNA image

This recipe builds public software for the bounded local executor. It is an
execution capability, not a method-selection recommendation or scientific,
expert, clinical, or regulatory qualification.

The workflow source is nf-core/differentialabundance 2.0.0 at
`30ed7741fc392127156c2fb10cfa3d69d216b54b`; Nextflow is 25.10.4 and nf-schema is
2.5.1. The full upstream source/config/module tree is retained unchanged.
Runtime engines, networking, parameters and resource boundaries are separately
enforced by the controller and its retained worker.

The admitted RNA graph requires shinyngs validation and static exploratory and
differential plotting even with `skip_reports=true` and Shiny app construction
disabled. It also requires DESeq2 normalization/differential analysis,
BiocParallel, limma, the custom R matrix and differential-table filters and csvtk
join. Optional Quarto reports, GTF preprocessing, external enrichment, GEO,
Affymetrix, MaxQuant and other estimator branches are not admitted capabilities.
Their source is retained, but their optional software is not claimed installed.

Upstream process environments specify mutually incompatible R generations;
copying those separate module pins into a single R library is not viable. The
native linux/arm64 environment lock instead resolves R 4.5.1, DESeq2 1.50.2,
shinyngs 2.4.0, limma 3.68.5, BiocParallel 1.44.0, csvtk 0.37.0, OpenJDK
21.0.9 and Python 3.12.13, pandas 2.3.3 and NumPy 2.2.6 plus all 272 resolved
software packages. Exact package
URLs, builds and SHA-256 values are in `package-lock.linux-arm64.json`. The
unmodified workflow still needs actual governed execution verification with
these versions. Package resolution or library loading does not establish it.
The first admitted image omitted pandas and failed at the mandatory custom
differential-table filter after four tasks completed; no validated result was
published. This dependency correction preserves all 265 original package builds
and adds seven locked packages. The recipe now checks Python, pandas and NumPy
native imports, the filter's table APIs, required R libraries and executables.

Run `prepare_context.py /absolute/staging/context` to download only these
checksum-locked public software assets into a narrow context. This stage uses up
to six downloads and refuses to start another download below 20 GiB free disk.
Build with `build_local.py /absolute/staging` after making the bundled Docker
buildx executable available under the staging directory's isolated
`docker-config/cli-plugins/`. The script uses only
`docker --host unix:///var/run/docker.sock`, requires the default Docker builder,
applies a 60-minute build deadline and 20 GiB disk floor, and builds with
`--network=none`. It reads no user Docker configuration and does not push images.
Build logs, local engine/builder evidence, terminal status, installed inventory,
asset manifest and image receipt remain in staging.

`/opt/devbench/admission.json` and label `devbench.community.inventory` contain
the same canonical inventory without an `image` field. The external environment
lock adds an independently observed immutable OCI repo digest. Embedding a final
image digest in its own filesystem or label would be self-referential. A Docker
config image ID is never substituted for a repository manifest digest.

The asset manifest maps absolute regular-file paths to SHA-256 and excludes its
own file and the admission file; the immutable image digest binds both. Tool
records identify the exact file or package directory used for their hash. Tree
hashes use canonical UTF-8 JSON of sorted relative POSIX regular-file paths to
SHA-256 values, excluding symlinks; immutable image identity also binds symlinks.
The Nextflow record's public path is the launch wrapper; `sha256_path` identifies
the upstream distribution file being hashed. Runtime rechecks must finish
before dispatch. No host socket, nested container engine or execution privilege
is required inside the image.

"""Historical DevBench imports for the shared pathways kernel."""

from rna_kernels.pathways import (
    MAX_MATRIX_BYTES as MAX_MATRIX_BYTES,
)
from rna_kernels.pathways import (
    MAX_REQUEST_BYTES as MAX_REQUEST_BYTES,
)
from rna_kernels.pathways import (
    MAX_RESULT_BYTES as MAX_RESULT_BYTES,
)
from rna_kernels.pathways import (
    SUMMARY_FORMAT as SUMMARY_FORMAT,
)
from rna_kernels.pathways import (
    execute_pathway_request as execute_pathway_request,
)
from rna_kernels.pathways import (
    main as main,
)
from rna_kernels.pathways import (
    run_mounted_pathways as run_mounted_pathways,
)

if __name__ == "__main__":
    raise SystemExit(main())

"""Verify admitted RNA tool imports and executables without scientific inputs."""
import io
import json
import shutil
import subprocess
import sys


def main():
    import numpy
    import pandas
    assert sys.version_info[:3] == (3,12,13)
    assert pandas.__version__ == '2.3.3' and numpy.__version__ == '2.2.6'
    frame=pandas.read_csv(io.StringIO('effect,q\n2,0.01\n-2,0.02\n0,\n'))
    selected=frame[frame.effect.notna() & frame.q.notna() & (frame.effect.abs()>=1) & (frame.q<=.05)]
    assert selected.effect.tolist()==[2,-2] and numpy.array([1,2]).sum()==3
    for name in ('bash','python','python3','R','Rscript','java','csvtk','pandoc','ps','awk','sed','grep','cut','tr','head','tail','find','cat','cp','mv','rm','mkdir','touch','date','sleep','chmod','stat','readlink','tee','mkfifo'):
        if not shutil.which(name): raise ValueError('Missing workflow executable: '+name)
    for name in ('validate_fom_components.R','exploratory_plots.R','differential_plots.R'):
        if not shutil.which(name): raise ValueError('Missing shinyngs executable: '+name)
    rcode='for(x in c("DESeq2","BiocParallel","limma","shinyngs","optparse","yaml","plotly","htmlwidgets","rmarkdown")){stopifnot(requireNamespace(x,quietly=TRUE));cat(x,as.character(packageVersion(x)),"\\n")};stopifnot(rmarkdown::pandoc_available(),capabilities("png"),capabilities("cairo"))'
    subprocess.run(['Rscript','-e',rcode],check=True)
    print(json.dumps({'python':sys.version.split()[0],'pandas':pandas.__version__,'numpy':numpy.__version__,'scope':'software imports, native extensions, table API and executable/renderer capability only; no expression data or fitting'}))


if __name__=='__main__': main()

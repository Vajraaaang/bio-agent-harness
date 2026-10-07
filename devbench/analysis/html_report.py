"""Offline Plotly views and nbconvert HTML with embedded notebook images."""
from __future__ import annotations

from html import escape
from html.parser import HTMLParser
import json
from pathlib import Path
import subprocess
import sys


def write_interactive_figures(store,conditions,output,*,batch_key):
    import numpy as np
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    from plotly.offline import get_plotlyjs
    from plotly.colors import qualitative
    from .figure_artifacts import _table
    from .qc_concordance import QC_FIELDS
    from .checkpoints import atomic_json

    output=Path(output)
    reference=_table(store,'qc','external-reference.csv')
    qc=_table(store,'qc','metrics.csv')
    if set(reference.index)!=set(qc.index):
        raise ValueError('reference_cell_identities_must_match_QC')
    reference=reference.reindex(qc.index)
    tables={name:_table(store,name+'_graph','embedding.csv') for name in conditions}
    for table in tables.values():
        if not set(table.index)<=set(qc.index):
            raise ValueError('embedding_cell_identities_missing_from_QC')
    embedding=make_subplots(rows=1,cols=len(tables),subplot_titles=list(tables))
    palette=qualitative.Alphabet+qualitative.Dark24
    groups=[]
    modes=('Observed batch','Rerun Leiden cluster','Stored cell-type prediction')
    for mode in modes:
        mode_labels={name:np.asarray(table[batch_key] if mode==modes[0] else table.leiden if mode==modes[1] else
                    reference.loc[table.index,'celltypes'] if 'celltypes' in reference else
                    np.repeat('unavailable',len(table))).astype(str) for name,table in tables.items()}
        levels=sorted({label for labels in mode_labels.values() for label in labels})
        colors={label:palette[index%len(palette)] for index,label in enumerate(levels)}
        for column,(name,table) in enumerate(tables.items(),start=1):
            labels=mode_labels[name]
            for label in sorted(set(labels)):
                selected=labels==label
                embedding.add_trace(go.Scattergl(x=table.UMAP_1.to_numpy()[selected].tolist(),y=table.UMAP_2.to_numpy()[selected].tolist(),
                    mode='markers',name=escape(label),legendgroup=escape(label),showlegend=column==1,
                    marker={'size':4,'opacity':.7,'color':colors[label]},
                    customdata=table.index.to_numpy()[selected].tolist(),meta={'grouping':mode,'condition':name},
                    hovertemplate=escape(label)+'<br>Cell ID=%{customdata}<br>UMAP 1=%{x:.3f}<br>UMAP 2=%{y:.3f}<extra></extra>',
                    visible=mode==modes[0]),row=1,col=column)
                groups.append(mode)
    embedding.update_layout(title='Uncorrected and integrated embeddings — visualization only',height=650,
        template='plotly_white',legend={'font':{'size':9}},
        updatemenus=[{'type':'dropdown','x':0,'y':1.15,'buttons':[
            {'label':mode,'method':'update','args':[{'visible':[group==mode for group in groups]}]}
            for mode in modes]}])
    embedding.update_xaxes(title_text='UMAP 1 (arbitrary units)')
    embedding.update_yaxes(title_text='UMAP 2 (arbitrary units)')
    comparisons=make_subplots(rows=2,cols=3,subplot_titles=[*QC_FIELDS,''])
    for index,field in enumerate(QC_FIELDS):
        row,column=divmod(index,3);row+=1;column+=1
        if field not in reference or field not in qc:
            continue
        old=reference[field].to_numpy(dtype=float);new=qc[field].to_numpy(dtype=float)
        mask=np.isfinite(old)&np.isfinite(new)
        comparisons.add_trace(go.Scattergl(x=old[mask].tolist(),y=new[mask].tolist(),mode='markers',showlegend=False,
            customdata=qc.index.to_numpy()[mask].tolist(),meta={'QC_field':field},
            marker={'size':3,'opacity':.45},hovertemplate='Cell ID=%{customdata}<br>Stored=%{x}<br>Recomputed=%{y}<extra></extra>'),row=row,col=column)
        if mask.any():
            lo=min(float(old[mask].min()),float(new[mask].min()));hi=max(float(old[mask].max()),float(new[mask].max()))
            comparisons.add_trace(go.Scatter(x=[lo,hi],y=[lo,hi],mode='lines',showlegend=False,
                line={'color':'#b13c2e','dash':'dash'},hoverinfo='skip'),row=row,col=column)
        comparisons.update_xaxes(title_text='Stored',row=row,col=column)
        comparisons.update_yaxes(title_text='Recomputed',row=row,col=column)
    comparisons.update_layout(title='QC concordance — all input cells with finite paired values',height=700,template='plotly_white')
    confidence=go.Figure()
    if 'conf_score' in reference:
        values=reference.conf_score.to_numpy(dtype=float)
        finite=np.isfinite(values)
        confidence.add_trace(go.Histogram(x=values[finite].tolist(),customdata=qc.index.to_numpy()[finite].tolist(),
                                         name='All input cells',opacity=.6,nbinsx=40))
        kept=qc.qc_pass.to_numpy(dtype=bool)&np.isfinite(values)
        confidence.add_trace(go.Histogram(x=values[kept].tolist(),customdata=qc.index.to_numpy()[kept].tolist(),
                                         name='QC-retained cells',opacity=.6,nbinsx=40))
    confidence.update_layout(title='Stored classifier confidence — calibration unknown',template='plotly_white',
        barmode='overlay',xaxis_title='Stored conf_score',yaxis_title='Cells',height=400)
    figures={'embedding':json.loads(embedding.to_json()),'QC':json.loads(comparisons.to_json()),
             'confidence':json.loads(confidence.to_json())}
    atomic_json(output/'interactive-data.json',figures)
    content=json.dumps(figures,allow_nan=False).replace('&','\\u0026').replace('<','\\u003c').replace('>','\\u003e')
    fragment=("<section><p>These plots show the recorded analysis and fixed external predictions. "
        "Concordance is not annotation quality. UMAP coordinates are not used for ASW metrics.</p>"
        '<script>'+get_plotlyjs()+'</script><script type="application/json" id="devbench-plot-data">'+content+'</script>'
        '<div id="devbench-embedding"></div><div id="devbench-QC"></div><div id="devbench-confidence"></div>'
        '<script>(function(){const figures=JSON.parse(document.getElementById("devbench-plot-data").textContent);'
        'for(const [key,fig] of Object.entries(figures)){Plotly.newPlot("devbench-"+key,fig.data,fig.layout,'
        '{responsive:true,displaylogo:false});}})();</script></section>')
    (output/'interactive.html').write_text(fragment)
    return {'plots':list(figures),'input_cells':len(qc),'embedding_cells':{k:len(v) for k,v in tables.items()},
            'subsampling':False,'plotly_javascript':'embedded_inline'}


class _HTMLAssets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.remote_resources=[];self.images=0;self.plot_data=[];self.in_plot_data=False
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='img':
            self.images+=1
        key='src' if tag in {'img','script','iframe','source'} else 'href' if tag=='link' else None
        if key and attrs.get(key) and not attrs[key].startswith('data:'):
            self.remote_resources.append({'tag':tag,'resource':attrs[key]})
        if tag=='script' and attrs.get('id')=='devbench-plot-data':
            self.in_plot_data=True
    def handle_data(self,data):
        if self.in_plot_data:
            self.plot_data.append(data)
    def handle_endtag(self,tag):
        if tag=='script':
            self.in_plot_data=False


def verify_html(root):
    from .checkpoints import sha256
    root=Path(root)
    parser=_HTMLAssets();parser.feed((root/'notebook.html').read_text())
    expected=json.loads((root/'figures/interactive-data.json').read_text())
    checks={'no_external_resource_dependencies':not parser.remote_resources,
            'embedded_notebook_images_present':parser.images>0,
            'interactive_plot_data_matches':bool(parser.plot_data) and json.loads(''.join(parser.plot_data))==expected}
    return {'passed':all(checks.values()),'checks':checks,'external_resources':parser.remote_resources,
            'html_sha256':sha256(root/'notebook.html'),'notebook_sha256':sha256(root/'notebook.ipynb'),
            'scope':'self_contained_resource_and_plot_data_verification_not_biological_validation'}


def export_notebook_html(root):
    from .resources import StageMeasurement
    from .checkpoints import atomic_json
    root=Path(root)
    command=[sys.executable,'-m','nbconvert','--to','html','--template','basic','--embed-images',
        '--output','notebook','--output-dir',str(root),str(root/'notebook.ipynb')]
    with StageMeasurement('HTML_export') as measured:
        result=subprocess.run(command,capture_output=True,text=True,timeout=120)
        (root/'html-export.log').write_text(result.stdout+result.stderr)
        result.check_returncode()
        fragment=(root/'notebook.html').read_text()
        style='body{font:15px system-ui,sans-serif;max-width:1500px;margin:32px auto;padding:0 24px;color:#15232b}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f6f7;padding:12px}img{max-width:100%}table{border-collapse:collapse}td,th{border:1px solid #cbd2d7;padding:7px;text-align:left}.output_area{margin:16px 0}.prompt{color:#647480}'
        (root/'notebook.html').write_text('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>scRNA analysis notebook</title><style>'+style+'</style></head><body>'+fragment+'</body></html>')
        review=verify_html(root)
    atomic_json(root/'html-export.json',{'command':command,'measurement':measured.result,'verification':review})
    if not review['passed']:
        raise ValueError('HTML export is not self-contained or does not match its plotting data')
    return review

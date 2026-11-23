"""Bulk artifact view lifecycle preserves an open scientific inspection."""
from tests.test_workbench_artifact_preview import DOM, run_js


def test_original_download_does_not_invalidate_open_plot_pdf():
    value = run_js(DOM + r"""
(async()=>{
const source='{}',artifact=descriptor('result.json',source,'a','application/json');let mounted;
artifact.plot_availability={available:true,capabilities:['bulk_ma'],source:{artifact_id:artifact.artifact_id,sha256:artifact.sha256}};
globalThis.BulkScientificFigure={mount:(host,options)=>{mounted=options;return {destroy(){}};}};
const workspace={source:{sha256:artifact.sha256,artifact_id:artifact.artifact_id},genes:[]};
f.mount(host,{artifacts:[artifact],request:async(path,body)=>new Response(path.endsWith('/scientific-plots')?JSON.stringify(workspace):path.endsWith('/scientific-pdf')?'%PDF-test':source)});
await all().find(n=>n.dataset.artifactAction==='scientific-plots').fire('click');
const selection={kind:'ma',gene_id:null,scope:'complete',view:{bounds:[0,1,0,1],filters:{},display_reference_lines:[]}};
await mounted.exportPDF(selection);
await all().find(n=>n.dataset.artifactAction==='download').fire('click');
const pdf=await mounted.exportPDF(selection);console.log(JSON.stringify({size:pdf.size}));
})();
""")
    assert value["size"] == 9

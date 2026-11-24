"""Bounded scientific artifact previews use actual bytes and explicit actions."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


MODULE = Path(__file__).resolve().parents[1] / "src/devbench/analysis/assets/workbench-artifacts.js"


def run_js(source):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for artifact module checks")
    result = subprocess.run([node, "-e", "const f=require(" + json.dumps(str(MODULE)) + ");\n" + source],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_csv_preserves_quoted_fields_crlf_newlines_and_escaped_quotes():
    source = 'gene,description,value\r\nG1,"two, fields",0\r\nG2,"line one\r\nline ""two""",-1.2\r\n'
    value = run_js("console.log(JSON.stringify(f.parseTable(" + json.dumps(source) + ",'csv')));")
    assert value["columns"] == ["gene", "description", "value"]
    assert value["rows"] == [["G1", "two, fields", "0"], ["G2", 'line one\r\nline "two"', "-1.2"]]
    assert value["totalRows"] == 2


def test_table_truncation_reports_actual_total_without_sampling():
    value = run_js("console.log(JSON.stringify(f.parseTable('gene\\n'+Array.from({length:250},(_,i)=>'G'+i).join('\\n'),'csv')));")
    assert value["totalRows"] == 250
    assert len(value["rows"]) == 200
    assert value["truncated"] is True
    assert value["rows"][-1] == ["G199"]


def test_tsv_and_json_rows_preserve_zero_false_null_and_missing():
    value = run_js("console.log(JSON.stringify([f.parseTable('gene\\tvalue\\nG1\\t0','tsv'),f.parseTable('[{\"gene\":\"G1\",\"value\":0,\"flag\":false},{\"gene\":\"G2\",\"value\":null}]','json')]));")
    assert value[0]["rows"] == [["G1", "0"]]
    assert value[1]["columns"] == ["gene", "value", "flag"]
    assert value[1]["rows"] == [["G1", 0, False], ["G2", None, {"not_supplied": True}]]


@pytest.mark.parametrize("value,kind", [
    ('gene,value\n"unclosed,1', 'csv'),
    ('gene,value\nG1,1,2', 'csv'),
    ('[{"v":1e400}]', 'json'),
    ('[{"v":NaN}]', 'json'),
    ('[{"v":1,"v":2}]', 'json'),
    ('[{"v":9007199254740993}]', 'json'),
])
def test_malformed_or_inexact_data_fails_without_silent_changes(value, kind):
    assert run_js("let refused=false;try{f.parseTable(" + json.dumps(value) + "," + json.dumps(kind) + ");}catch(e){refused=true;}console.log(JSON.stringify(refused));") is True


def test_oversized_text_is_rejected_before_parsing():
    assert run_js("let refused=false;try{f.parseTable('x'.repeat(256*1024+1),'csv');}catch(e){refused=true;}console.log(JSON.stringify(refused));") is True


def test_only_strict_local_artifact_descriptors_are_accepted():
    value = run_js("const a={artifact_id:'a'.repeat(64),name:'plot.png',content_type:'image/png',artifact_type:'file',size_bytes:10,sha256:'b'.repeat(64),download_path:'/api/scientific/artifacts/'+'a'.repeat(64)};console.log(JSON.stringify([f.validateDescriptor(a).name,...['https://example.com/a','/api/scientific/artifacts/'+'b'.repeat(64),'/api/scientific/artifacts/../secret'].map(path=>{try{f.validateDescriptor({...a,download_path:path});return false;}catch(e){return true;}})]));")
    assert value == ["plot.png", True, True, True]


def test_stream_reader_cancels_before_accepting_oversized_payload():
    value = run_js("(async()=>{let cancelled=false,reads=0;const response={headers:{get:()=>null},body:{getReader:()=>({read:async()=>{reads++;return {done:false,value:new Uint8Array(20)};},cancel:async()=>{cancelled=true;}})}};let refused=false;try{await f.readBounded(response,10);}catch(e){refused=true;}console.log(JSON.stringify({refused,cancelled,reads}));})();")
    assert value == {"refused": True, "cancelled": True, "reads": 1}


def test_stream_reader_rejects_declared_excess_without_reading():
    value = run_js("(async()=>{let touched=false;const response={headers:{get:()=> '100'},body:{cancel:async()=>{touched=true;}}};let refused=false;try{await f.readBounded(response,10);}catch(e){refused=true;}console.log(JSON.stringify({refused,touched}));})();")
    assert value == {"refused": True, "touched": True}


def test_notebook_preview_contains_only_actual_code_with_explicit_cap():
    value = run_js("console.log(JSON.stringify(f.notebookCode(JSON.stringify({cells:[{cell_type:'markdown',source:['Do not execute']},{cell_type:'code',execution_count:2,source:['print(0)\\n']},{cell_type:'code',source:['x'.repeat(160000)]}]}))));")
    assert "Do not execute" not in value["source"]
    assert "print(0)" in value["source"]
    assert value["truncated"] is True
    assert len(value["source"]) <= 150000


DOM = r"""
const crypto=require('node:crypto');globalThis.crypto=crypto.webcrypto;
let created=[],revoked=[],requests=[];
URL.createObjectURL=blob=>{const url='blob:test-'+created.length;created.push(url);return url;};
URL.revokeObjectURL=url=>revoked.push(url);
class Element{
 constructor(tag){this.tagName=tag;this.children=[];this.attributes={};this.events={};this.dataset={};this.className='';this.textContent='';this.ownerDocument=doc;this.classList={toggle:()=>{}};}
 append(...nodes){for(const node of nodes){node.parentNode=this;this.children.push(node);}}
 replaceChildren(...nodes){this.children=[];this.append(...nodes);}
 setAttribute(key,value){this.attributes[key]=value;}
 addEventListener(key,callback){(this.events[key]??=[]).push(callback);}
 async fire(key){for(const callback of this.events[key]||[])await callback();}
 click(){return this.fire('click');}
 remove(){if(this.parentNode)this.parentNode.children=this.parentNode.children.filter(node=>node!==this);}
}
let doc={createElement:tag=>new Element(tag)};doc.body=new Element('body');const host=new Element('div');
const all=(node=host)=>[node,...node.children.flatMap(child=>all(child))];
const content=(node=host)=>node.textContent+node.children.map(child=>content(child)).join(' ');
function descriptor(name,source,id='a',mime='text/csv'){return {artifact_id:id.repeat(64),name,content_type:mime,artifact_type:'file',size_bytes:Buffer.byteLength(source),sha256:crypto.createHash('sha256').update(source).digest('hex'),download_path:'/api/scientific/artifacts/'+id.repeat(64)};}
const preview=id=>all().find(node=>node.dataset.artifactAction==='preview'&&node.dataset.artifactId===id.repeat(64));
"""


def test_mount_fetches_nothing_until_explicit_preview_and_displays_actual_values():
    value = run_js(DOM + "(async()=>{const data='gene,value\\nG1,0',artifact=descriptor('result.csv',data);const c=f.mount(host,{artifacts:[artifact],request:async path=>{requests.push(path);return new Response(data);}});const initial=requests.length;await preview('a').fire('click');console.log(JSON.stringify({initial,requests,text:content()}));c.destroy();})();")
    assert value["initial"] == 0
    assert value["requests"] == ["/api/scientific/artifacts/" + "a" * 64]
    assert "G1" in value["text"]
    assert "1 shown of 1 rows" in value["text"]


def test_late_preview_never_replaces_newer_artifact():
    value = run_js(DOM + "(async()=>{const first='gene\\nOLD',second='gene\\nNEW';let resolve;const delayed=new Promise(r=>resolve=r);f.mount(host,{artifacts:[descriptor('first.csv',first),descriptor('second.csv',second,'b')],request:path=>path.endsWith('a'.repeat(64))?delayed:Promise.resolve(new Response(second))});const pending=preview('a').fire('click');await preview('b').fire('click');resolve(new Response(first));await pending;console.log(JSON.stringify(content(all().find(n=>n.className==='wa-artifact-viewer'))));})();")
    assert "NEW" in value and "OLD" not in value


def test_preview_image_urls_are_revoked_on_selection_change_and_destroy():
    value = run_js(DOM + "(async()=>{const image=Buffer.from([137,80,78,71,13,10,26,10]);const a=descriptor('first.png',image,'a','image/png'),b=descriptor('second.png',image,'b','image/png');const c=f.mount(host,{artifacts:[a,b],request:async()=>new Response(image)});await preview('a').fire('click');await preview('b').fire('click');c.destroy();console.log(JSON.stringify({created,revoked,remaining:host.children.length}));})();")
    assert value == {"created": ["blob:test-0", "blob:test-1"], "revoked": ["blob:test-0", "blob:test-1"], "remaining": 0}


def test_hash_mismatch_and_svg_masquerading_as_raster_are_refused():
    value = run_js(DOM + "(async()=>{const source='<svg onload=alert(1) />';const a=descriptor('false.png',source,'a','image/png'),b={...descriptor('tampered.csv','gene\\nORIGINAL','b'),size_bytes:9};f.mount(host,{artifacts:[a,b],request:async path=>new Response(path.endsWith('a'.repeat(64))?source:'gene\\nEVIL')});await preview('a').fire('click');const rasterError=content();await preview('b').fire('click');console.log(JSON.stringify({rasterError,hashError:content(),created}));})();")
    assert "do not match the declared raster" in value["rasterError"]
    assert "SHA-256 does not match" in value["hashError"]
    assert value["created"] == []


def test_matrix_preview_is_disabled_and_large_explicit_download_does_not_fetch():
    value = run_js(DOM + "(async()=>{const matrix=descriptor('counts.h5ad','x'),large={...descriptor('archive.zip','x','b','application/zip'),size_bytes:65*1024*1024};f.mount(host,{artifacts:[matrix,large],request:async()=>{requests.push(1);return new Response('x');}});const disabled=preview('a').disabled;await all().find(n=>n.dataset.artifactAction==='download'&&n.dataset.artifactId==='b'.repeat(64)).fire('click');console.log(JSON.stringify({disabled,requests,text:content()}));})();")
    assert value["disabled"] is True and value["requests"] == []
    assert "existing export or CLI" in value["text"]


def test_verified_scientific_json_result_download_extends_without_widening_preview_budget():
    value = run_js(DOM + r"""(async()=>{
      const data=Buffer.alloc(96000000,32);data.write('{"result":"');data.write('"}',data.length-2);
      const artifact={...descriptor('gene-stage.json',data,'a','application/json'),scientific_ref:{object_type:'result',object_id:'c'.repeat(32),version_id:'d'.repeat(64)}};
      const c=f.mount(host,{artifacts:[artifact],request:async path=>{requests.push(path);return new Response(data);}});
      const previewDisabled=preview('a').disabled;await preview('a').fire('click');const afterPreview=requests.length;
      await all().find(n=>n.dataset.artifactAction==='download').fire('click');
      console.log(JSON.stringify({previewDisabled,afterPreview,requests,created,text:content()}));c.destroy();
    })();""")
    assert value['previewDisabled'] is True and value['afterPreview'] == 0
    assert value['requests'] == ['/api/scientific/artifacts/' + 'a' * 64]
    assert value['created'] == ['blob:test-0']
    assert 'Verified download prepared' in value['text']


def test_scientific_result_above_decimal_96mb_is_rejected_before_fetch():
    value = run_js(DOM + r"""(async()=>{
      const artifact={...descriptor('gene-stage.json','{}','a','application/json'),size_bytes:96000001,scientific_ref:{object_type:'result',object_id:'c'.repeat(32),version_id:'d'.repeat(64)}};
      f.mount(host,{artifacts:[artifact],request:async()=>{requests.push(1);return new Response('{}');}});
      await all().find(n=>n.dataset.artifactAction==='download').fire('click');
      console.log(JSON.stringify({requests,created,text:content()}));
    })();""")
    assert value['requests'] == [] and value['created'] == []
    assert 'existing export or CLI' in value['text']


def test_actual_json_object_is_not_confused_with_missing_value_display_marker():
    value = run_js(DOM + "(async()=>{const data='[{\"gene\":\"G1\",\"flag\":{\"not_supplied\":true}},{\"gene\":\"G2\"}]';f.mount(host,{artifacts:[descriptor('table.json',data,'a','application/json')],request:async()=>new Response(data)});await preview('a').fire('click');console.log(JSON.stringify(content()));})();")
    assert '{"not_supplied":true}' in value
    assert "Not supplied" in value


def test_generic_json_does_not_advertise_plots_without_verified_saved_capabilities():
    value = run_js(DOM + r"""(async()=>{
      const data='{"status":"no saved plots"}';f.mount(host,{artifacts:[descriptor('plain.json',data,'a','application/json')],request:async path=>{requests.push(path);return new Response(data);}});
      console.log(JSON.stringify({plotButtons:all().filter(n=>['bulk-plots','scientific-plots'].includes(n.dataset.artifactAction)).length,requests}));
    })();""")
    assert value == {'plotButtons': 0, 'requests': []}


def test_verified_prefit_plot_availability_enables_only_its_bound_scientific_route():
    value = run_js(DOM + r"""(async()=>{
      const data='{"format":"saved-prefit-fixture"}',a=descriptor('population-audit.json',data,'a','application/json');
      a.plot_availability={available:true,capabilities:['scrna_population_cell_counts'],reason:null,source:{artifact_id:a.artifact_id,sha256:a.sha256}};
      let mounted=0;globalThis.BulkScientificFigure={mount(node,options){mounted++;return {destroy(){}};}};
      f.mount(host,{artifacts:[a],request:async path=>{requests.push(path);return new Response(JSON.stringify({format:'devbench.scientific-plot-workspace/1',source:{artifact_id:a.artifact_id,sha256:a.sha256},panels:[]}));}});
      const buttons=all().filter(n=>['bulk-plots','scientific-plots'].includes(n.dataset.artifactAction));const count=buttons.length;
      await buttons.find(n=>n.dataset.artifactAction==='scientific-plots').fire('click');console.log(JSON.stringify({count,requests,mounted}));
    })();""")
    assert value == {'count': 1, 'requests': ['/api/scientific/artifacts/' + 'a' * 64 + '/scientific-plots'], 'mounted': 1}


def test_restricted_raw_artifact_still_offers_its_registered_plot():
    value = run_js(DOM + r"""(async()=>{
      const a=descriptor('result.json','{}','a','application/json');
      a.download_status='restricted_audit_entitlement_required';
      a.plot_availability={available:true,capabilities:['scrna_population_cell_counts'],source:{artifact_id:a.artifact_id,sha256:a.sha256}};
      f.mount(host,{mode:'figures',artifacts:[a],request:async path=>{requests.push(path);return new Response('{}');}});
      const controls=Object.fromEntries(all().filter(n=>n.dataset.artifactAction).map(n=>[n.dataset.artifactAction,n]));
      await controls.download.fire('click');
      console.log(JSON.stringify({preview:controls.preview.disabled,download:controls.download.disabled,
        plot:!!controls['scientific-plots'],requests}));
    })();""")
    assert value == {'preview': True, 'download': True, 'plot': True, 'requests': []}


def test_actual_saved_population_capability_contract_enables_its_exact_artifact(tmp_path):
    from devbench.analysis.bulk_plot_service import scientific_artifact_plot_availability
    from tests.test_plot_adapters_scrna_population import ScopedScientific

    scientific = ScopedScientific(tmp_path)
    availability = scientific_artifact_plot_availability(scientific, scientific.identity)
    assert availability['available'] is True
    assert set(availability['capabilities']) == {'scrna_population_cell_counts', 'scrna_population_contrast_support'}
    artifact = {'artifact_id': scientific.identity, 'sha256': scientific.identity,
        'name': 'saved-design.json', 'content_type': 'application/json', 'artifact_type': 'file',
        'size_bytes': scientific.path.stat().st_size, 'download_path': '/api/scientific/artifacts/' + scientific.identity,
        'plot_availability': availability}
    value = run_js(DOM + r"""
      const artifact=ARTIFACT;f.mount(host,{artifacts:[artifact],request:async path=>{requests.push(path);return new Response('{}');}});
      console.log(JSON.stringify({buttons:all().filter(n=>n.dataset.artifactAction==='scientific-plots').map(n=>n.dataset.artifactId),requests}));
    """.replace('ARTIFACT', json.dumps(artifact)))
    assert value == {'buttons': [scientific.identity], 'requests': []}


@pytest.mark.parametrize('availability', [
    {'available': False, 'capabilities': ['scrna_population_cell_counts']},
    {'available': True, 'capabilities': []},
    {'available': True, 'capabilities': ['scrna_population_cell_counts'], 'source': {'artifact_id': 'b' * 64, 'sha256': 'a' * 64}},
    {'available': True, 'capabilities': ['scrna_population_cell_counts'], 'source': {'artifact_id': 'a' * 64, 'sha256': 'b' * 64}},
    {'available': True, 'capabilities': [None]},
])
def test_missing_unavailable_or_mismatched_plot_metadata_cannot_enable_explore(availability):
    value = run_js(DOM + r"""
      const a=descriptor('result.json','{}','a','application/json');a.plot_availability=AVAILABILITY;
      f.mount(host,{artifacts:[a],request:async path=>{requests.push(path);return new Response('{}');}});
      console.log(JSON.stringify({plotButtons:all().filter(n=>['bulk-plots','scientific-plots'].includes(n.dataset.artifactAction)).length,requests}));
    """.replace('AVAILABILITY', json.dumps(availability)))
    assert value == {'plotButtons': 0, 'requests': []}


def test_episode_artifacts_use_only_the_current_authenticated_job_route():
    value = run_js(DOM + r"""(async()=>{
      const job='c'.repeat(32),data=JSON.stringify({findings:[{gene_id:'G1',p_value:0,q_value:null}]});
      const a={kind:'episode_output',job_id:job,name:'synthesis',download_url:'/api/agentic-episodes/'+job+'/download/synthesis'};
      f.mount(host,{context:{job_id:job},artifacts:[a,{...a,job_id:'d'.repeat(32)},{...a,download_url:'https://example.com/stolen'}],request:async path=>{requests.push(path);return new Response(data);}});
      const previews=all().filter(n=>n.dataset.artifactAction==='preview');assert.equal(previews.length,1);await previews[0].fire('click');
      console.log(JSON.stringify({requests,text:content(),unsafe:all().some(n=>n.innerHTML!==undefined),raw:all().some(n=>n.tagName==='pre')}));
    })();""")
    assert value["requests"] == ["/api/agentic-episodes/" + "c" * 32 + "/download/synthesis"]
    assert "G1" in value["text"] and "P value" in value["text"]
    assert value["unsafe"] is False
    assert value["raw"] is False


def test_episode_plot_preview_binds_current_job_and_retains_pdf_route():
    value = run_js(DOM + r"""(async()=>{
      const job='c'.repeat(32),data='{}',base='/api/agentic-episodes/'+job;
      const a={kind:'episode_output',job_id:job,name:'result',download_url:base+'/download/result',sha256:crypto.createHash('sha256').update(data).digest('hex'),size_bytes:2};let mounted=0,pdf;
      globalThis.BulkScientificFigure={mount(node,options){mounted++;assert.equal(options.loadCapabilities,undefined);assert.equal(options.exportBundle,undefined);pdf=options.exportPDF;return {destroy(){}};}};
      f.mount(host,{context:{job_id:job},artifacts:[a],request:async(path,body)=>{requests.push({path,body});return new Response(body?'PDF':JSON.stringify({format:'devbench.scientific-plot-workspace/1',source:{run_id:job,sha256:a.sha256},panels:[]}));}});
      await all().find(n=>n.dataset.artifactAction==='scientific-plots').fire('click');await pdf({kind:'ma',scope:'complete'});
      console.log(JSON.stringify({requests,mounted}));
    })();""")
    assert value["mounted"] == 1
    assert [item["path"] for item in value["requests"]] == [
        "/api/agentic-episodes/" + "c" * 32 + "/scientific-plots",
        "/api/agentic-episodes/" + "c" * 32 + "/scientific-pdf",
    ]


def test_episode_artifact_does_not_downgrade_malformed_digest_metadata():
    value = run_js(DOM + r"""
      const job='c'.repeat(32),a={kind:'episode_output',job_id:job,name:'synthesis',download_url:'/api/agentic-episodes/'+job+'/download/synthesis',sha256:'invalid',size_bytes:2};
      f.mount(host,{context:{job_id:job},artifacts:[a],request:async()=>new Response('{}')});console.log(JSON.stringify(all().filter(n=>n.dataset.artifactAction==='preview').length));
    """)
    assert value == 0

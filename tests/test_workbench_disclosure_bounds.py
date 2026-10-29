"""Deep evidence stays readable without recursive fixed-width key columns."""
from pathlib import Path

from tests.test_workbench_conversation_redesign import run_js


def test_nested_clarification_evidence_uses_full_width_sections_and_keeps_values():
    run_js(r"""
class Node {constructor(tag){this.tagName=tag;this.children=[];this.attrs={};this.open=false;}append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.children=nodes;}setAttribute(k,v){this.attrs[k]=String(v);}addEventListener(k,f){this[k]=f;} }
globalThis.document={createElement:tag=>new Node(tag),createElementNS:(ns,tag)=>new Node(tag)};
const longValue='https://example.invalid/retained-source/'+ 'sample_identifier_'.repeat(24);
const content={clarification_fields:[{field:'assay',label:'Assay',type:'select',options:[{value:'scrna',label:'Single-cell RNA sequencing'}]}],source:longValue};
const investigation={id:'a'.repeat(32),status:'completed_saved_evidence',question:'Inspect retained context',turns:[{kind:'clarification_request',title:'Recorded context',content}]};
const before=JSON.stringify(investigation),container=new Node('div');
api.renderInvestigation(container,{investigation,config:{}});
const walk=n=>[n,...n.children.flatMap(walk)],nodes=walk(container),has=(n,c)=>(n.className||'').split(' ').includes(c);
const nested=nodes.filter(n=>has(n,'wc-kv-nested'));
assert.ok(nested.length>=4);
assert.ok(nested.every(n=>has(n,'wc-kv-stacked')),'Every nested record must use a full-width stack.');
assert.ok(nodes.some(n=>has(n,'wc-kv-section-label')&&n.textContent==='Clarification fields'));
assert.ok(nodes.some(n=>n.textContent==='scrna'));
assert.ok(nodes.some(n=>n.textContent==='Single-cell RNA sequencing'));
assert.ok(nodes.some(n=>n.textContent===longValue));
const body=nodes.find(n=>has(n,'wc-disclosure-body'));
assert.equal(body.attrs.tabindex,'0');assert.equal(body.attrs.role,'region');
const raw=nodes.find(n=>n.tagName==='textarea'&&n.value===JSON.stringify(content,null,2));
assert.ok(raw&&raw.readOnly);
assert.equal(JSON.stringify(investigation),before);
assert.ok(nodes.every(n=>n.innerHTML===undefined));
""")


def test_disclosures_and_native_pickers_have_readable_responsive_bounds():
    theme=(Path(__file__).resolve().parents[1]/'devbench/analysis/assets/workbench-theme.css').read_text()
    assert '.wc-kv-grid.wc-kv-stacked' in theme
    assert 'grid-template-columns: minmax(0,1fr)' in theme
    assert 'container-type: inline-size' in theme
    assert '@container (max-width:360px)' in theme
    assert 'max-height: min(440px,60dvh)' in theme
    assert 'max-inline-size: calc(100vw - 24px)' in theme
    assert 'white-space: normal' in theme
    assert 'overflow-wrap: anywhere' in theme

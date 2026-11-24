"""Check the local Research Studio opener through the MCP Apps messages."""

from devbench.platform.plugin_mcp_ui import card_html
from tests.test_workbench_conversation_redesign import run_js

HOST = r"""
class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.attrs = {}; }
  append(...items) { this.children.push(...items); }
  replaceChildren() { this.children = []; }
  addEventListener(type, callback) { this[type] = callback; }
  setAttribute(key, value) { this.attrs[key] = value; }
}
const main = new Element('main'), footerNode = new Element('footer');
const byTag = (node, tag) => node.tagName === tag ? node :
  node.children.map(item => byTag(item, tag)).find(Boolean);
const messages = [], handlers = {}, scheduled = new Map();
const parent = {postMessage: (message, origin) => messages.push([message, origin])};
const host = 'https://host.example';
globalThis.window = {parent, addEventListener: (type, next) => handlers[type] = next};
globalThis.document = {body:{dataset:{card:'plan-card'}},
  createElement: tag=>new Element(tag),
  querySelector: tag => tag === 'main' ? main : footerNode};
let timer = 0;
globalThis.setTimeout = callback => {scheduled.set(++timer, callback); return timer;};
globalThis.clearTimeout = id => scheduled.delete(id);
const deliver = body => handlers.message({source:parent,origin:host,
  data:{jsonrpc:'2.0',...body}});
const address = 'http://127.0.0.1:8765/?session_id='+'a'.repeat(32)+'&view=workflows';
const plan = (url, execution = {}) => deliver({method:'ui/notifications/tool-result',
  params:{structuredContent:{
  status:'local_studio_only', plan_id:'b'.repeat(32),studio_url:url,...execution,
}}});
"""


def run_card(script):
    resource = card_html("plan-card")
    javascript = resource.split("<script>", 1)[1].split("</script>", 1)[0]
    run_js(HOST + javascript + script)


def test_studio_card_keeps_copyable_link_on_denial_or_timeout():
    run_card(r"""
deliver({id:1, result:{}});
plan(address);
const control = byTag(footer,'button');
const fallback = byTag(footer,'details'), field = byTag(fallback,'input');
assert.equal(control.textContent,'Open in Research Studio');
assert.equal(field.readOnly,true);
assert.equal(field.value,address);
control.click();
let [request, origin] = messages.at(-1);
assert.equal(request.method,'ui/open-link');
assert.deepEqual(request.params,{url:address});
assert.equal(origin,host);
deliver({id:request.id, error:{code:-32000,message:'Link opening denied by user'}});
assert.equal(fallback.open,true);
assert.match(byTag(footer,'p').textContent,/local address/);
assert.equal(control.disabled,false);
fallback.open = false;
control.click();
scheduled.get(timer)();
assert.equal(fallback.open,true);
assert.equal(control.disabled,false);
fallback.open = false;
control.click();
[request] = messages.at(-1);
deliver({id:request.id, result:{isError:true}});
assert.equal(fallback.open,true);
fallback.open = false;
control.click();
[request] = messages.at(-1);
deliver({id:request.id, result:{}});
assert.equal(fallback.open,false);
assert.match(byTag(footer,'p').textContent,/browser/);
""")


def test_studio_card_does_not_present_links_outside_exact_loopback_route():
    run_card(r"""
deliver({id:1, result:{}});
for (const malformed of [
  address.replace('127.0.0.1','example.invalid'),
  address.replace('http:','https:'),
  address+'&next=https://example.invalid',
  address+'&view=findings',
  address+'#fragment',
]) {
  plan(malformed);
  assert.equal(byTag(footer,'button'),undefined);
  assert.equal(byTag(footer,'input'),undefined);
}
""")


def test_studio_card_distinguishes_private_review_from_execution():
    run_card(r"""
deliver({id:1, result:{}});
plan(address, {
  execution_route:null, execution_blocker:'private_plan_execution_unavailable',
});
assert.match(byTag(body,'p').textContent,/no supported execution route/i);
assert.equal(byTag(footer,'button').textContent,'Open in Research Studio');

plan(address, {execution_route:'offline_studio'});
assert.match(byTag(body,'p').textContent,/review and approve the local run/i);

plan(address);
assert.doesNotMatch(byTag(body,'p').textContent,/approve the local run/i);
""")

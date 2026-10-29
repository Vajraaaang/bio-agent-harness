"""Generate and serve an explicit synthetic-only browser display benchmark.

No model, expression matrix, scientific method, or historical run is executed.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "reports/workbench-redesign/2026-10-28/performance"
MODULE = ROOT / "devbench/analysis/assets/scientific-figure.js"
COLORS = {"A": "#0072b2", "B": "#d55e00", "C": "#009e73", "D": "#cc79a7"}


def generate(output: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    output.mkdir(parents=True, exist_ok=True)
    for n in (50000, 100000):
        rows = [{"observation_id": "c" + str(i),
                 "x": i % 500 + math.sin(i * .7) * .2,
                 "y": i // 500 + math.cos(i * .3) * .2,
                 "color": "ABCD"[i % 4], "metadata": {},
                 "status": "rendered", "exclusion_reason": None} for i in range(n)]
        panel = {"panel_id": "synthetic", "title": "Synthetic software workload · " + format(n, ",") + " observations",
                 "x_axis": {"field": "x", "label": "Synthetic X", "unit": "arbitrary", "transform": "none"},
                 "y_axis": {"field": "y", "label": "Synthetic Y", "unit": "arbitrary", "transform": "none"},
                 "color": {"kind": "categorical", "field": "software_category", "label": "Synthetic category",
                           "grouping": "software_category", "colors": COLORS}, "rows": rows, "labels": []}
        payload = {"format": "devbench.figure-data/1", "payload_kind": "observation_points",
                   "key_columns": ["observation_id"], "population_ids": [row["observation_id"] for row in rows],
                   "panels": [panel]}
        record = {"format": "devbench.figure/1", "family": "G08", "figure_id": "software-benchmark-" + str(n),
                  "analysis_n": n, "rendered_n": n,
                  "semantics": {"unit": "cell", "representation_or_layer": "synthetic_software_geometry_not_biology",
                                "limitations": ["Software benchmark only. No biological result, UMAP fitting or scientific validation."]},
                  "display_options": {}}
        (output / (str(n) + ".json")).write_text(json.dumps({"record": record, "payload": payload}, separators=(",", ":")))
        fig, ax = plt.subplots(figsize=(8, 5), dpi=120)
        for category, color in COLORS.items():
            members = [row for row in rows if row["color"] == category]
            ax.scatter([row["x"] for row in members], [row["y"] for row in members], s=4, alpha=.8, c=color, label=category, linewidths=0)
        ax.set(xlabel="Synthetic X", ylabel="Synthetic Y", title=panel["title"])
        ax.legend(title="Synthetic category", fontsize=8)
        fig.tight_layout()
        fig.savefig(output / (str(n) + ".png"))
        plt.close(fig)
    (output / "index.html").write_text(PAGE)


PAGE = r'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Scientific figure performance · synthetic software benchmark</title>
<style>
:root{--surface:#fff;--ink:#183239;--muted:#52696e;--line:#d7e2e4}*{box-sizing:border-box}body{margin:0;background:#f3f6f6;color:var(--ink);font:14px/1.5 system-ui,sans-serif}main{max-width:1200px;margin:auto;padding:24px}h1{font-size:24px;margin:0 0 8px}h2{font-size:18px}.note{background:#e5f0ed;padding:12px;border-radius:5px}.controls,.sf-toolbar,.sf-actions,.sf-exports,.sf-paging,.sf-table-tools{display:flex;gap:10px;flex-wrap:wrap;align-items:center}button,select,input{font:inherit}button,select{padding:8px;border:1px solid #b7c9ce;border-radius:4px;background:#fff;color:var(--ink)}button{cursor:pointer}button:disabled{opacity:.5}button:focus-visible,input:focus-visible,select:focus-visible,canvas:focus-visible{outline:3px solid #0072b2;outline-offset:3px}.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;background:#fff;padding:18px;margin:18px 0;border:1px solid var(--line)}.metrics p{margin:0}.metrics strong{display:block;font-size:20px}.sf-heading{display:flex;gap:12px;align-items:center}.tag{font-size:11px;background:#e5f0ed;padding:4px 7px}.sf-scope,.sf-context,.sf-hover,.sf-counts,.sf-selection{color:var(--muted);font-size:12px}.sf-toolbar{padding:12px 0}.sf-control{display:flex;flex-direction:column;gap:4px;font-size:11px}.sf-plot{width:100%;overflow:hidden}.sf-canvas{display:block;width:100%;height:auto;touch-action:none}.scientific-figure{padding:18px;background:#fff;border:1px solid var(--line)}.sf-legend{display:flex;gap:14px;align-items:center;flex-wrap:wrap}.sf-legend>p{flex-basis:100%;margin:5px 0}.sf-category{display:flex;gap:6px;align-items:center}.sf-category input[type=color]{width:28px;height:24px;padding:0;border:0}.sf-setting-grid{display:flex;gap:18px;padding:10px 0}.sf-table-scroll{overflow:auto}.sf-table{width:100%;border-collapse:collapse;font-size:12px}.sf-table td,.sf-table th{text-align:left;padding:7px;border-bottom:1px solid var(--line)}.sf-evidence pre{overflow:auto;font-size:11px}.sf-table-details,.sf-evidence,.sf-settings{margin:15px 0}.sf-exports{margin:15px 0;font-size:12px}#baseline img{max-width:100%;height:auto}.sf-colorbar{height:8px;width:180px;background:linear-gradient(to right,#f7fbff,#c6dbef,#6baed6,#2171b5,#08306b)}.scientific-figure:fullscreen{overflow:auto}.sf-paging{padding:10px 0}@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto}}
</style>
<main><h1>Scientific figure display benchmark</h1><p class="note">Synthetic software workloads only. These points are deterministic geometry, not biological observations or fitted UMAPs. Static image decoding and interactive rendering provide different capabilities; timings do not establish a speedup or scientific efficacy.</p>
<div class="controls"><label>Workload <select id="size"><option value="50000">50,000 observations</option><option value="100000">100,000 observations</option></select></label><button id="load">Load workload</button><button id="image" disabled>Measure original static PNG</button><button id="interactive" disabled>Measure interactive mount</button><button id="redraw" disabled>Measure redraw</button><button id="selection" disabled>Measure exact selection</button><button id="csv" disabled>Measure saved-table CSV</button><button id="save" disabled>Save observed metrics</button></div>
<p id="status" role="status" aria-live="polite">Choose a synthetic workload, then load its saved point geometry.</p>
<div id="metrics" class="metrics"></div><div id="baseline" hidden></div><div id="figure"></div>
<p>Image baseline: matplotlib PNG of the exact same point identities and coordinates. No browser point selection existed in that image. Interactive workload: actual production figure module. Browser timings include scheduling and are single observations; repeat to assess cold/warm variation.</p></main>
<script src="/scientific-figure.js"></script><script>
'use strict';
const el=id=>document.getElementById(id);let fixture=null,controller=null,metrics={},runs=[];
const frame=()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
function show(){el('metrics').replaceChildren();for(const [label,value]of Object.entries(metrics)){const p=document.createElement('p');p.append(document.createTextNode(label));const strong=document.createElement('strong');strong.textContent=typeof value==='number'?value.toLocaleString(undefined,{maximumFractionDigits:2}):value;p.append(strong);el('metrics').append(p);}el('save').disabled=false;}
function record(kind,values){runs.push({kind,at:new Date().toISOString(),...values});Object.assign(metrics,values);show();}
async function perform(button,fn){button.disabled=true;try{await fn();}catch(error){el('status').textContent=error.message;}finally{button.disabled=false;}}
el('load').onclick=()=>perform(el('load'),async()=>{controller?.destroy();controller=null;el('baseline').hidden=true;el('figure').replaceChildren();el('redraw').disabled=true;el('status').textContent='Loading synthetic saved geometry…';const started=performance.now(),response=await fetch('/'+el('size').value+'.json',{cache:'no-store'}),raw=await response.text();fixture=JSON.parse(raw);metrics={};record('workload_load',{population:fixture.record.analysis_n,payload_bytes:new TextEncoder().encode(raw).byteLength,fetch_parse_ms:performance.now()-started});for(const id of ['image','interactive','selection','csv'])el(id).disabled=false;el('status').textContent='Loaded '+fixture.record.analysis_n.toLocaleString()+' synthetic observations. No sampling.';});
el('image').onclick=()=>perform(el('image'),async()=>{el('baseline').hidden=false;const img=new Image(),started=performance.now();img.src='/'+fixture.record.analysis_n+'.png?attempt='+Date.now();await img.decode();el('baseline').replaceChildren(img);record('static_image',{static_image_decode_ms:performance.now()-started,static_image_width:img.naturalWidth,static_image_height:img.naturalHeight});el('status').textContent='Decoded the original static PNG; it has no exact point selection capability.';});
el('interactive').onclick=()=>perform(el('interactive'),async()=>{el('baseline').hidden=true;const started=performance.now();controller=ScientificFigure.mount(el('figure'),{...fixture,context:{label:'Synthetic benchmark only · '+fixture.record.analysis_n+' points'},onSelection:data=>{el('status').textContent='Explicit handoff: '+data.observation_ids.length+' exact IDs. No analysis submitted.';}});await frame();record('interactive_mount',{interactive_mount_first_draw_ms:performance.now()-started,...controller.getPerformance()});el('redraw').disabled=false;el('status').textContent='Mounted actual interactive renderer. All saved points remain available; use controls and the paged table.';});
el('redraw').onclick=()=>perform(el('redraw'),async()=>{const started=performance.now();controller.redraw();await frame();record('interactive_redraw',{redraw_with_scheduling_ms:performance.now()-started,...controller.getPerformance()});el('status').textContent='Measured one redraw with scheduling. This is not a sustained FPS measurement.';});
el('selection').onclick=()=>perform(el('selection'),async()=>{const prepared=ScientificFigure.prepare(fixture.record,fixture.payload),panel=prepared.panels[0],b=panel.bounds,mid=(b[0]+b[1])/2,polygon=[[b[0],b[2]],[mid,b[2]],[mid,b[3]],[b[0],b[3]]],expected=panel.rows.filter(row=>row.x<=mid).map(row=>row.observation_id),started=performance.now(),ids=ScientificFigure.selectPolygon(panel,polygon,new Set(Object.keys(panel.color.colors))),elapsed=performance.now()-started;const matches=ids.length===expected.length&&ids.every((id,index)=>id===expected[index]);record('exact_selection',{exact_selection_ms:elapsed,selected_id_count:ids.length,expected_id_count:expected.length,exact_identity_match:matches?'PASS':'FAIL'});el('status').textContent=(matches?'PASS':'FAIL')+': exact identity selection '+ids.length.toLocaleString()+' / expected '+expected.length.toLocaleString()+'. No display sampling.';});
el('csv').onclick=()=>perform(el('csv'),async()=>{const started=performance.now(),csv=ScientificFigure.toCSV(fixture.payload.panels[0].rows);record('csv_export',{saved_table_csv_ms:performance.now()-started,csv_bytes:new TextEncoder().encode(csv).byteLength});el('status').textContent='Measured complete saved-table CSV serialization; no filesystem download is implied.';});
el('save').onclick=()=>perform(el('save'),async()=>{const response=await fetch('/metrics',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({format:'workbench.synthetic-browser-performance/1',scope:'software_only_no_scientific_claim',user_agent:navigator.userAgent,viewport:{width:innerWidth,height:innerHeight,dpr:devicePixelRatio},runs})});if(!response.ok)throw Error('Metrics save failed');el('status').textContent='Saved observed browser metrics in browser-observations.json.';});
</script></html>'''


def serve(output: Path, port: int) -> None:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, code: int, content: bytes, kind: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self) -> None:
            route = self.path.split("?", 1)[0]
            files = {"/": (output / "index.html", "text/html;charset=utf-8"),
                     "/scientific-figure.js": (MODULE, "text/javascript;charset=utf-8")}
            for n in (50000, 100000):
                files["/" + str(n) + ".json"] = (output / (str(n) + ".json"), "application/json")
                files["/" + str(n) + ".png"] = (output / (str(n) + ".png"), "image/png")
            if route not in files:
                self.reply(404, b"Unavailable benchmark resource", "text/plain")
                return
            path, kind = files[route]
            self.reply(200, path.read_bytes(), kind)

        def do_POST(self) -> None:
            origin = "http://127.0.0.1:" + str(port)
            if self.path != "/metrics" or self.headers.get("Origin") != origin or self.headers.get("Content-Type") != "application/json":
                self.reply(403, b"Use the local benchmark page", "text/plain")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 65536:
                    raise ValueError("Metric body exceeds bound")
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict) or data.get("format") != "workbench.synthetic-browser-performance/1":
                    raise ValueError("Unknown metrics format")
                encoded = json.dumps(data, indent=2, allow_nan=False)
                (output / "browser-observations.json").write_text(encoded)
            except (ValueError, TypeError, OSError):
                self.reply(400, b"Invalid finite metric record", "text/plain")
                return
            self.reply(200, b'{"saved":true}', "application/json")

    server = HTTPServer(("127.0.0.1", port), Handler)
    print("Synthetic software benchmark: http://127.0.0.1:" + str(port) + "/", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--port", type=int, default=8879)
    parser.add_argument("--serve", action="store_true")
    args = parser.parse_args()
    if not args.output.is_absolute() or args.output != args.output.resolve() or not 0 < args.port < 65536:
        parser.error("Use an absolute canonical output and valid loopback port")
    generate(args.output)
    if args.serve:
        serve(args.output, args.port)


if __name__ == "__main__":
    main()

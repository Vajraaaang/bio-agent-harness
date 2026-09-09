"""Measured task outcomes, explicit missing values, and flat SHA-256 receipts."""
import argparse
from collections import Counter
import hashlib
import html
import json
from pathlib import Path


def _cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ")[:1200]


def markdown_report(results):
    lines = ["# DevBench execution leaderboard", "",
             "| Task ID | Analytical track | Model condition | Pass/fail | Execution time (s) | Memory peak (MB) | Grader reasoning |",
             "|---|---|---|---|---:|---:|---|"]
    taxonomy = Counter()
    accepted = completed = passed = executed = 0
    for run in results:
        meta = run.get("metadata", {})
        grade = run.get("grader_result")
        accepted += meta.get("accepted_final") is True
        completed += isinstance(grade, dict)
        passed += run.get("passed") is True
        executed += meta.get("script_execution_observed") is True
        failure = meta.get("failure_type")
        if failure:
            taxonomy[failure] += 1
        duration = meta.get("elapsed_sec")
        memory = meta.get("peak_memory_mb")
        model = meta.get("actual_model") or "model unknown"
        status = ("PASS" if run.get("passed") else "FAIL") if grade else "NOT GRADED"
        reason = grade.get("reasoning") if grade else meta.get("error", "No grade available")
        values = [run.get("task_id", "unknown"), meta.get("track", "unknown"), model + " / " + meta.get("condition", "unknown"),
                  status, f"{duration:.2f}" if isinstance(duration, (int, float)) else "unknown",
                  f"{memory:.2f}" if isinstance(memory, (int, float)) else "unknown", reason]
        lines.append("| " + " | ".join(_cell(value) for value in values) + " |")
    total = len(results)
    lines += ["", f"Attempts: {total}. Observed successful script executions: {executed}. Accepted finals: {accepted}. Completed grades: {completed}.",
              f"Pass rate over attempted tasks: {passed}/{total}" + (f" ({passed/total:.1%})." if total else " (unknown)."),
              f"Pass rate among completed grades: {passed}/{completed}" + (f" ({passed/completed:.1%})." if completed else " (unknown)."), "",
              "Failure taxonomy (recorded categories only): " + (", ".join(f"{kind}: {count}" for kind, count in sorted(taxonomy.items())) or "none recorded"),
              "", "Ungraded zeros are failure bookkeeping. Categories are not inferred from prose. SDK workspace traces are participant-written, not authenticated tool telemetry.",
              "Missing model identity, complete token usage, subscription charges and memory observations remain unknown. No timing, model-improvement or clinical-validation guarantee is implied."]
    return "\n".join(lines) + "\n"


def html_report(results, source_sha256):
    """A standalone leaderboard for flat execution records, without legacy CAS UI."""
    def escaped(value):
        return html.escape(str(value), quote=True)
    rows, details = [], []
    for run in results:
        meta, grade = run.get("metadata", {}), run.get("grader_result")
        state = ("PASS" if run.get("passed") else "FAIL") if isinstance(grade, dict) else "NOT GRADED"
        duration, memory = meta.get("elapsed_sec"), meta.get("peak_memory_mb")
        values = [run.get("task_id", "unknown"), meta.get("track", "unknown"),
                  (meta.get("actual_model") or "model unknown") + " / " + meta.get("condition", "unknown"), state,
                  f"{duration:.2f}" if isinstance(duration, (int, float)) else "unknown",
                  f"{memory:.2f}" if isinstance(memory, (int, float)) else "unknown",
                  grade.get("reasoning", "No rationale recorded") if isinstance(grade, dict) else meta.get("error", "No grade available")]
        rows.append('<tr>' + ''.join('<td>' + escaped(value) + '</td>' for value in values) + '</tr>')
        receipts = ''.join('<li><strong>' + escaped(name) + '</strong><code>' + escaped(item.get('sha256', 'unknown')) + '</code></li>'
                           for name, item in meta.get('artifacts', {}).items() if isinstance(item, dict) and item.get('preserved'))
        details.append('<details><summary>' + escaped(run.get('task_id', 'unknown')) + ' · execution evidence</summary><p>Successful script execution observed: '
                       + escaped(meta.get('script_execution_observed', 'unknown')) + '. Inference session observed: '
                       + escaped(meta.get('inference_session_observed', 'unknown')) + '.</p><ul>' + receipts
                       + '</ul><p>These are saved artifact hashes. SDK workspace traces are participant-written evidence.</p></details>')
    accepted = sum(r.get('metadata', {}).get('accepted_final') is True for r in results)
    graded = sum(isinstance(r.get('grader_result'), dict) for r in results)
    passed = sum(r.get('passed') is True for r in results)
    summary = markdown_report(results).split('Attempts:', 1)[-1]
    return '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>DevBench · Code-as-Action leaderboard</title><style>
body{margin:0;background:#f4f2e9;color:#172a31;font:15px/1.6 system-ui,sans-serif}main{max-width:1400px;margin:auto;padding:48px 5vw}header{border-bottom:1px solid #b8c3bd;padding-bottom:30px}h1{font:normal 52px/1.1 Georgia,serif;margin:15px 0}h2{font:normal 28px Georgia,serif}small{letter-spacing:.12em;text-transform:uppercase}p{max-width:920px}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:#647774;margin:32px 0}.metric{padding:24px;background:#172a31;color:#f4f2e9}.metric strong{font:42px Georgia,serif;display:block}.table-scroll{overflow:auto;background:#fff}table{border-collapse:collapse;min-width:1050px;width:100%;font-size:13px}th,td{text-align:left;padding:17px 14px;border-bottom:1px solid #dce0d8;vertical-align:top}th{background:#e5e9df}td:last-child{min-width:290px;max-width:490px;overflow-wrap:anywhere}code{display:block;font-size:12px;overflow-wrap:anywhere}details{padding:18px 0;border-bottom:1px solid #c8d0c8}summary{cursor:pointer;font-weight:600}li{margin:10px 0}.notes{white-space:pre-wrap;font:13px/1.8 ui-monospace,monospace;max-width:100%;overflow-wrap:anywhere}footer{padding:25px 0;color:#546763;font-size:12px}@media(max-width:700px){h1{font-size:39px}.metrics{grid-template-columns:1fr 1fr}main{padding:28px 5vw}}</style></head><body><main>
<header><small>DevBench / Code-as-Action</small><h1>Measured execution.</h1><p>Recorded tasks, script outcomes and deterministic grades. Missing answers remain missing; an ungraded zero is failure bookkeeping.</p></header>
<section class="metrics" aria-label="Recorded outcomes">''' + ''.join('<div class="metric">' + escaped(label) + '<strong>' + str(value) + '</strong></div>' for label, value in
        [('Task attempts', len(results)), ('Accepted finals', accepted), ('Completed grades', graded), ('Passed tasks', passed)]) + '''</section>
<div class="table-scroll"><table><caption>Recorded benchmark outcomes</caption><thead><tr>''' + ''.join('<th scope="col">' + label + '</th>' for label in
        ['Task ID', 'Analytical track', 'Model condition', 'Pass/fail', 'Time (s)', 'Peak RSS (MB)', 'Grader reasoning']) + '</tr></thead><tbody>' + ''.join(rows) + '''</tbody></table></div>
<h2>Evidence and interpretation</h2><p class="notes">Attempts:''' + escaped(summary) + '</p>' + ''.join(details) + '''
<footer>Source JSON SHA-256<code>''' + escaped(source_sha256) + '''</code><p>Export file hashes are verified and saved in the separate receipts JSON. Hashes describe artifact bytes, not authenticated signatures. RSS is sampled process-tree resident memory in decimal MB; shared pages and between-poll peaks limit interpretation. Self-contained HTML with no network requests.</p></footer></main></body></html>'''


def write_reports(results, output_json, output_html, output_md=None):
    output_json, output_html = Path(output_json).resolve(), Path(output_html).resolve()
    for path in (output_json, output_html):
        path.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(results, indent=2, allow_nan=False) + "\n")
    output_html.write_text(html_report(results, hashlib.sha256(output_json.read_bytes()).hexdigest()))
    paths = [output_json, output_html]
    if output_md is not None:
        output_md = Path(output_md).resolve()
        output_md.parent.mkdir(parents=True, exist_ok=True)
        output_md.write_text(markdown_report(results))
        paths.append(output_md)
    receipts = {"format": "devbench.flat-artifact-receipts/1", "scope": "final file bytes, not an authenticated signature or execution DAG",
                "files": [{"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size} for path in paths]}
    for item in receipts["files"]:
        assert hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest() == item["sha256"]
    receipt_path = output_json.with_suffix(".receipts.json")
    receipt_path.write_text(json.dumps(receipts, indent=2) + "\n")
    return {"json": str(output_json), "html": str(output_html), "markdown": str(output_md) if output_md else None, "receipts": str(receipt_path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    results = json.loads(args.results.read_text())
    print(markdown_report(results))
    write_reports(results, args.output.with_suffix(".json"), args.output.with_suffix(".html"), args.output.with_suffix(".md"))


if __name__ == "__main__":
    main()

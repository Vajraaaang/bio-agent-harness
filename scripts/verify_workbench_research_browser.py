"""Verify synthetic Workbench research flows through a real local Chromium page.

Run this script in a Python environment with Playwright and Chromium installed.
Pass --runtime-python for the project's Python and --image for an already
mounted, pinned single-cell kernel image. The evidence directory must be fresh.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import signal
import subprocess
import sys
import threading
import time
import zipfile
from contextlib import contextmanager, suppress
from pathlib import Path
from urllib.parse import urlencode, urlsplit

PROJECT = Path(__file__).resolve().parents[1]


@contextmanager
def local_server(root, python, image):
    ready = root / "browser-fixture.json"
    log_path = root / "workbench.log"
    with (
        log_path.open("wb") as log,
        subprocess.Popen(
            [
                python,
                __file__,
                "--root",
                str(root),
                "--image",
                image,
                "--fixture-server",
            ],
            cwd=PROJECT,
            stdout=log,
            stderr=subprocess.STDOUT,
        ) as process,
    ):
        try:
            deadline = time.monotonic() + 180
            while not ready.is_file():
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError(
                        f"Synthetic Workbench failed to start. See {log_path}."
                    )
                time.sleep(0.2)
            yield json.loads(ready.read_text())
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


def post(page, endpoint, control, *, expected_status=200):
    with page.expect_response(
        lambda response: response.url.endswith(endpoint)
        and response.request.method == "POST",
        timeout=60_000,
    ) as observed:
        control.click()
    response = observed.value
    result = response.json()
    assert response.status == expected_status, result
    return result


def item(panel, text):
    return panel.locator(".research-item").filter(has_text=text)


def aim_card(panel, aim_id):
    assert re.fullmatch(r"[a-z0-9-]+", aim_id)
    return panel.locator(f'.research-item[data-aim-id="{aim_id}"]')


def section(parent, title):
    detail = parent.locator("summary").filter(
        has_text=re.compile(f"^{re.escape(title)}$")
    )
    detail.click()
    return detail.locator("..")


def screenshot(page, target, root, filename):
    target.scroll_into_view_if_needed()
    page.screenshot(path=root / filename, animations="disabled")


def verify_drawer(page, digest, expect):
    drawer = page.locator("#receipt-drawer")
    expect(drawer).to_be_visible()
    expect(drawer.get_by_text(digest, exact=True)).to_be_visible()
    return drawer


def close_drawer(drawer, expect):
    drawer.get_by_role("button", name="Close receipt drawer", exact=True).click()
    expect(drawer).to_be_hidden()


def verify_canvas_tabs(page, expect):
    tabs = page.locator("#canvas-tabs")
    expect(tabs).to_be_visible()
    labels = [
        label.strip() for label in tabs.locator('[role="tab"]').all_text_contents()
    ]
    expected_labels = [
        "Overview", "Findings", "Workflows", "Figures", "Code", "Evidence",
    ]
    assert labels == expected_labels, labels
    clipped = tabs.evaluate(
        """strip => [...strip.querySelectorAll('[role="tab"]')]
        .filter(button => button.getClientRects().length)
        .filter(button => {
            const range = document.createRange();
            range.selectNodeContents(button);
            const label = range.getBoundingClientRect();
            const tab = button.getBoundingClientRect();
            const rail = strip.getBoundingClientRect();
            return label.left < Math.max(rail.left, tab.left) - 1 ||
                   label.right > Math.min(rail.right, tab.right) + 1 ||
                   label.height > 1.5 * parseFloat(getComputedStyle(button).fontSize);
        }).map(button => button.textContent.trim())"""
    )
    assert not clipped, clipped


def verify_plan_metrics(page, root, expect):
    page.evaluate(
        """() => {
          const fixture = document.createElement('section');
          fixture.id = 'browser-plan-metrics-fixture';
          fixture.style.maxWidth = '680px';
          document.body.append(fixture);
          const investigation = {
            id: 'synthetic-metrics', revision: 4, status: 'approved', job_id: 'sample',
            job_state: {
              status: 'approved', proposal: {
                preview:{cells:80,genes:60},
                configuration:{
                  parameters:{workflow:'scrna_qc_integration_annotation/1',
                    correction:'none',memory_mb:1024},run_timeout_seconds:180
                }
              }
            },
            turns:[{id:'plan',kind:'plan_review',step:3,
              title:'Plan prepared',content:{}}]
          };
          WorkbenchConversation.renderInvestigation(fixture, {
            investigation, config: {execution_allowed:true}
          });
          fixture.querySelectorAll('details').forEach(detail => {
            detail.open = true;
          });
        }"""
    )
    fixture = page.locator("#browser-plan-metrics-fixture")
    grid = fixture.locator(".wc-plan-metrics").first
    expect(grid).to_have_css("display", "grid")
    expect(grid).to_have_css("gap", "6px")
    expect(grid.locator(".wc-plan-metric dd").first).to_have_css(
        "font-variant-numeric", "tabular-nums"
    )
    assert grid.evaluate(
        "node => getComputedStyle(node).gridTemplateColumns.split(' ').length"
    ) > 1
    grid.screenshot(path=root / "chat-plan-metrics.png", animations="disabled")
    fixture.evaluate("node => node.remove()")


def verify_appearance(page, root, expect):
    stages = page.get_by_role("tablist", name="Investigation stages")
    stages.get_by_role("tab", name="Review findings", exact=True).click()
    activity = page.locator("#activity-control")
    activity.click()
    dialog = page.locator("#project-dialog")
    expect(dialog).to_be_visible()
    activity_tabs = dialog.get_by_role("tablist", name="Project activity")
    expect(activity_tabs.get_by_role("tab")).to_have_text(
        ["Plans", "Runs", "Results", "Exports", "Aims & packages"]
    )
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    theme = page.locator("#theme-select")
    for mode in ("light", "dark"):
        theme.select_option(mode)
        expect(page.locator("html")).to_have_attribute("data-theme", mode)
        verify_canvas_tabs(page, expect)
        page.screenshot(path=root / f"studio-{mode}.png", animations="disabled")
    theme.select_option("system")
    for mode in ("dark", "light"):
        page.emulate_media(color_scheme=mode)
        expect(page.locator("html")).to_have_attribute("data-theme", mode)
    theme.select_option("light")

    for width in (861, 1040, 1120, 1180):
        page.set_viewport_size({"width": width, "height": 900})
        expect(page.locator(".inspector-close").first).to_be_hidden()
        verify_canvas_tabs(page, expect)
    page.set_viewport_size({"width": 1040, "height": 900})
    divider = page.locator("#investigation-divider")
    divider.press("Home")
    expect(divider).to_have_attribute(
        "aria-valuenow", divider.get_attribute("aria-valuemin")
    )
    verify_canvas_tabs(page, expect)
    page.screenshot(path=root / "studio-narrow.png", animations="disabled")
    divider.press("End")
    expect(divider).to_have_attribute(
        "aria-valuenow", divider.get_attribute("aria-valuemax")
    )
    verify_canvas_tabs(page, expect)
    page.screenshot(path=root / "studio-split-resized.png", animations="disabled")
    page.set_viewport_size({"width": 1440, "height": 1080})
    divider.dblclick()
    verify_plan_metrics(page, root, expect)


def verify_lineage(page, panel, root, expect):
    studio = panel.locator("[data-studio-lineage]")
    graph = studio.locator("[data-lineage-graph]")
    listing = studio.locator("[data-lineage-list]")
    graph_button = studio.get_by_role("button", name="Graph", exact=True)
    list_button = studio.get_by_role("button", name="List", exact=True)
    graph_button.click()
    expect(graph_button).to_have_attribute("aria-pressed", "true")
    expect(graph).to_be_visible()
    expect(listing).to_be_hidden()
    node = graph.locator('[data-lineage-step="qc-lanes"]')
    expect(node).to_be_visible()
    expect(node.locator(".lineage-generation")).to_have_css("white-space", "nowrap")
    node.click()
    expect(node).to_have_attribute("data-lineage-relation", "selected")
    downstream = graph.locator('[data-lineage-step="prepare-counts"]')
    expect(downstream).to_have_attribute("data-lineage-relation", "downstream")
    inspector = studio.locator("[data-lineage-inspector]")
    expect(inspector.get_by_role("button", name="Send step to chat")).to_be_visible()
    screenshot(page, graph, root, "adaptive-lineage-dag.png")
    list_button.click()
    expect(list_button).to_have_attribute("aria-pressed", "true")
    expect(graph).to_be_hidden()
    expect(listing.locator('[data-lineage-step="qc-lanes"]')).to_be_visible()


def verify_qc(page, panel, fixture, root, expect):
    endpoint = "/api/scientific/single-cell/" + fixture["session_id"]
    qc = item(panel, "Step qc-lanes")
    prepared = item(panel, "Step prepare-counts")
    expect(qc).to_have_attribute("data-status", "succeeded")
    expect(prepared).to_have_attribute("data-status", "succeeded")
    with page.expect_response(
        lambda response: response.url.endswith(
            endpoint + "?qc_step=qc-lanes&generation=1"
        )
    ) as observed:
        qc.get_by_role("button", name="Review QC", exact=True).click()
    response = observed.value
    assert response.ok, response.json()
    evidence = response.json()
    assert evidence["receipt_sha256"] == fixture["qc_receipt_sha256"]
    assert evidence["report"]["cells_input"] == 320
    assert {row["batch"] for row in evidence["report"]["thresholds"]} == {"A", "B"}
    thresholds = qc.get_by_role("table", name="Per-capture QC thresholds", exact=True)
    expect(thresholds).to_be_visible()
    rail = panel.locator(".studio-qc-stage-rail")
    expect(rail).to_have_css("list-style-type", "none")
    expect(rail).to_have_css("padding-inline-start", "0px")
    assert rail.locator(".studio-qc-stage").evaluate_all(
        """items => items.every(item => {
          const rail = item.parentElement.getBoundingClientRect();
          const stage = item.getBoundingClientRect();
          const bounds = [...item.children].map(child => child.getBoundingClientRect());
          return stage.left >= rail.left && stage.right <= rail.right + 1 &&
                 bounds.every((box, i) =>
                   box.right <= stage.right &&
                   (i === 0 || bounds[i - 1].bottom <= box.top)
                 );
        })"""
    )
    kpis = qc.locator(".studio-qc-kpis")
    expect(kpis).to_have_css("background-color", "rgba(0, 0, 0, 0)")
    assert float(kpis.evaluate("node => parseFloat(getComputedStyle(node).gap)")) >= 4
    receipt = evidence["receipt_sha256"]
    short = kpis.locator(f'code[title="{receipt}"]')
    expect(short).to_have_text(receipt[:12])
    for batch in ("A", "B"):
        expect(thresholds.get_by_text(batch, exact=True)).to_be_visible()
    screenshot(page, thresholds, root, "single-cell-qc-cockpit.png")
    qc.get_by_role("button", name="Inspect QC receipt", exact=True).click()
    drawer = verify_drawer(page, fixture["qc_receipt_sha256"], expect)
    screenshot(page, drawer, root, "qc-receipt-drawer.png")
    close_drawer(drawer, expect)
    diagnostics = qc.get_by_text("QC diagnostics and thresholds", exact=True)
    expect(diagnostics).to_be_visible()
    screenshot(page, diagnostics, root, "single-cell-qc-evidence.png")
    control = qc.get_by_role("button", name="Queue QC reanalysis", exact=True)
    qc.get_by_label("min genes", exact=True).fill("15")
    grant = qc.get_by_label("Allow this QC revision to reopen downstream analysis")
    expect(grant).not_to_be_checked()
    impact = qc.locator(".studio-qc-impact")
    dependent = impact.get_by_text(re.compile("prepare-counts.*superseded", re.I))
    expect(dependent).to_be_visible()
    control.click()
    expect(qc.get_by_text("Generation 1", exact=True)).to_be_visible()
    grant.check()
    revised = post(page, endpoint, control)
    expect(qc.get_by_text("Generation 2", exact=True)).to_be_visible()
    expect(prepared).to_have_attribute("data-status", "blocked")
    superseded = panel.get_by_text(
        re.compile(r"prepare-counts.*(?:tombstone|Superseded result)", re.I)
    )
    expect(superseded).to_be_visible()
    screenshot(page, superseded, root, "single-cell-reanalysis.png")
    return revised


def advance(page, panel, endpoint, expect):
    action = panel.get_by_role("button", name="Advance workflow", exact=True)
    data = panel.get_by_label("Allow data execution for this step")
    model = panel.get_by_label("Allow model selection for this step")
    expect(action).to_be_disabled()
    expect(data).not_to_be_checked()
    expect(model).not_to_be_checked()
    data.check()
    expect(action).to_be_disabled()
    model.check()
    result = post(page, endpoint, action)
    expect(
        panel.get_by_text(re.compile(r"Workflow updated\. Steps dispatched"))
    ).to_be_visible()
    return result["steps_dispatched"]


def fill_outcome(form, outcome):
    form.get_by_label("Outcome status", exact=True).select_option(outcome["status"])
    form.get_by_label("Outcome rationale", exact=True).fill(outcome["rationale"])
    form.get_by_label("Uncertainty", exact=True).fill(outcome["uncertainty"])
    evidence = form.get_by_label("Evidence receipts", exact=True)
    options = [
        evidence.locator("option").filter(has_text=entry["receipt_sha256"][:12])
        for entry in outcome["evidence"]
    ]
    evidence.select_option([option.get_attribute("value") for option in options])


def verify_export(page, panel, root, package, filename):
    with page.expect_download() as exported:
        panel.get_by_role(
            "button", name="Export research package (.zip)", exact=True
        ).click()
    downloaded = root / filename
    exported.value.save_as(downloaded)
    with zipfile.ZipFile(downloaded) as archive:
        receipt = archive.read("thesis-package-receipt.json")
        assert hashlib.sha256(receipt).hexdigest() == package["root_sha256"]
        package_files = json.loads(receipt)["files"]
        names = set(archive.namelist())
        assert len(archive.namelist()) == len(names)
        assert names == {"thesis-package-receipt.json", *package_files}
        for name, checksum in package_files.items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == checksum
    return len(names)


def verify_thesis(page, panel, fixture, root, expect):
    endpoint = "/api/scientific/thesis/" + fixture["session_id"]
    aim = aim_card(panel, fixture["aim_id"])
    expect(aim).to_be_visible()
    expect(
        panel.get_by_text("Clinical release is disabled.", exact=True)
    ).to_be_visible()
    question = page.locator("#question")
    draft = question.input_value()
    aim.get_by_role("button", name="Cite aim in chat", exact=True).click()
    context = page.locator('#studio-context [data-kind="aim"]')
    expect(context.get_by_text("@aim", exact=True)).to_be_visible()
    expect(question).to_have_value(draft)
    context.get_by_role("button", name=re.compile(r"^Remove ")).click()
    expect(context).to_be_hidden()
    receipt = fixture["outcome"]["evidence"][0]["receipt_sha256"]
    current = section(aim, "Current evidence receipts")
    expect(current.get_by_text(re.compile(receipt))).to_be_visible()

    definition = dict(fixture["new_aim"])
    form = section(panel, "New aim")
    for name, key in (
        ("Aim ID", "aim_id"),
        ("Research question", "question"),
        ("Estimand", "estimand"),
    ):
        form.get_by_label(name, exact=True).fill(definition.pop(key))
    form.get_by_role("combobox", name=re.compile(r"^Aim kind")).select_option(
        definition.pop("kind")
    )
    advanced = section(form, "Inputs, dependencies, and multiplicity")
    advanced.get_by_label("Additional aim fields (JSON)").fill(json.dumps(definition))
    proposed = post(
        page, endpoint, form.get_by_role("button", name="Propose aim", exact=True)
    )
    new_aim = aim_card(panel, fixture["new_aim"]["aim_id"])
    expect(new_aim).to_have_attribute("data-status", "operator_review")
    form = section(new_aim, "Approve aim")
    form.get_by_label("Approval rationale").fill(
        "Approve the scoped synthetic fixture aim."
    )
    approved = post(
        page, endpoint, form.get_by_role("button", name="Approve aim", exact=True)
    )
    assert approved["revision"] > proposed["revision"]
    expect(new_aim).to_have_attribute("data-status", "ready")

    form = section(aim, "Open blinded review")
    form.get_by_label("Masked packet path").fill(fixture["packet"])
    form.get_by_label("Independent reviewer IDs (one per line)").fill(
        "\n".join(fixture["reviewers"])
    )
    outcome = fixture["outcome"]
    fill_outcome(form, outcome)
    form.get_by_label("Review rationale").fill("Freeze the synthetic masked packet.")
    opened = post(
        page,
        endpoint,
        form.get_by_role("button", name="Open blinded review", exact=True),
    )
    assert opened["panel"]["submitted_reviews"] == 0
    panel_state = aim.get_by_text(
        re.compile("Blinded panel.*awaiting independent reviews")
    )
    expect(panel_state).to_be_visible()
    screenshot(page, panel_state, root, "thesis-review-pending.png")

    form = section(aim, "Record outcome")
    fill_outcome(form, outcome)
    pending = post(
        page,
        endpoint,
        form.get_by_role("button", name="Record outcome", exact=True),
        expected_status=409,
    )
    assert pending["error"] == "OperationBlocked"
    expect(panel_state).to_be_visible()
    expect(form.get_by_label("Outcome rationale", exact=True)).to_have_value(
        outcome["rationale"]
    )
    expect(
        panel.get_by_role("button", name="Reload current evidence", exact=True)
    ).to_be_visible()

    subject = opened["panel"]["subject_sha256"]
    packet_dir = Path(fixture["packet"]).parent
    for index, template in enumerate(fixture["assessments"], start=1):
        expected = {**template, "subject_sha256": subject}
        assessment = packet_dir / f"fixture-assessment-{index}.json"
        assessment.write_text(json.dumps(expected) + "\n")
        form = section(aim, "Submit assessment")
        form.get_by_label("Verified assessment path").fill(str(assessment))
        admitted = post(
            page,
            endpoint,
            form.get_by_role("button", name="Submit assessment", exact=True),
        )
        assert admitted["panel"]["submitted_reviews"] == index
    assert admitted["panel"]["status"] == "passed"
    expect(aim.get_by_text(re.compile("Blinded panel.*passed"))).to_be_visible()
    form = section(aim, "Record outcome")
    fill_outcome(form, outcome)
    post(page, endpoint, form.get_by_role("button", name="Record outcome", exact=True))
    expect(aim).to_have_attribute("data-status", "supported")
    evidence = section(aim, "Outcome evidence")
    expect(evidence.get_by_text(re.compile(receipt))).to_be_visible()
    screenshot(page, evidence, root, "thesis-outcome-evidence.png")

    form = section(panel, "Build & Export Research Package (.zip)")
    form.get_by_label("Answer schema path").fill(fixture["schema"])
    form.get_by_label("Answer schema SHA-256", exact=True).fill(
        fixture["schema_sha256"]
    )
    form.get_by_label("Synthesis question", exact=True).fill(
        "Which fixture records support this synthetic exercise?"
    )
    form.get_by_label("Package outcome", exact=True).select_option("partial")
    form.get_by_role("button", name="Add field binding", exact=True).click()
    for name, value in (
        ("Answer field", "cells_input"),
        ("Source step ID", "qc-lanes"),
        ("Output port", "qc_report"),
        ("JSON field path", "cells_input"),
    ):
        form.get_by_label(name, exact=True).fill(value)
    response = post(
        page,
        endpoint,
        form.get_by_role(
            "button", name="Build & Export Research Package (.zip)", exact=True
        ),
    )
    package = response["package"]
    package_status = panel.get_by_text(
        f"Research package built · SHA-256 {package['root_sha256'][:12]} · "
        f"package {package['package_id']}",
        exact=True,
    )
    expect(package_status).to_be_visible()
    destination = Path(package["directory"])
    assert destination.is_relative_to(root)
    expect(panel.get_by_text(str(destination), exact=False)).to_have_count(0)
    receipt_bytes = (destination / "thesis-package-receipt.json").read_bytes()
    assert hashlib.sha256(receipt_bytes).hexdigest() == package["root_sha256"]
    manifest_bytes = (destination / "provenance_manifest.json").read_bytes()
    package_files = json.loads(receipt_bytes)["files"]
    assert (
        hashlib.sha256(manifest_bytes).hexdigest()
        == package_files["provenance_manifest.json"]
    )
    manifest = json.loads(manifest_bytes)
    assert manifest["clinical_release_allowed"] is False
    assert manifest["fields"]["cells_input"]["generation"] == 2
    assert package["answer_status"] == "validated"
    assert json.loads((destination / "final_discovery.json").read_text()) == {
        "cells_input": 320,
    }
    file_count = verify_export(page, panel, root, package, "thesis-evidence.zip")
    screenshot(page, package_status, root, "thesis-package.png")
    return {
        "panel": "passed",
        "assessments": 2,
        "package": package,
        "verified_export": file_count,
    }


def verify_thesis_matrix(page, project, fixture, root, expect):
    matrix_control = project.get_by_role("button", name="Evidence Matrix", exact=True)
    matrix_control.click()
    matrix = project.get_by_role("table", name="Aim Evidence Matrix", exact=True)
    expect(matrix).to_be_visible()
    viewport = matrix.locator("..").bounding_box()
    portfolio = project.locator(".thesis-aim-groups").first.bounding_box()
    assert viewport is not None and portfolio is not None
    assert viewport["width"] >= portfolio["width"] * 0.95
    supported = matrix.get_by_role("row").filter(has_text=fixture["aim_id"])
    new_aim_id = fixture["new_aim"]["aim_id"]
    new_aim = matrix.get_by_role("row").filter(has_text=new_aim_id)
    expect(supported).to_be_visible()
    expect(new_aim).to_be_visible()
    receipt = fixture["outcome"]["evidence"][0]["receipt_sha256"]
    evidence = section(supported, "1 receipt")
    token = evidence.locator(".studio-receipt-token").first
    expect(token).to_have_css("white-space", "nowrap")
    evidence.get_by_role("button", name=re.compile(receipt[:8])).click()
    close_drawer(verify_drawer(page, receipt, expect), expect)
    screenshot(page, matrix, root, "thesis-evidence-matrix.png")
    new_aim.get_by_role("button", name="Open aim " + new_aim_id).click()
    expect(page.locator("#project-dialog")).to_be_hidden()
    details = page.locator("#investigation-adaptive-panel")
    expect(aim_card(details, new_aim_id)).to_be_focused()
    page.locator("#activity-control").click()
    page.locator("#project-aims-button").click()
    project.get_by_role("button", name="Board", exact=True).click()
    expect(matrix).to_be_hidden()


def verify_thesis_board(project, expect):
    board = project.locator(".thesis-aim-groups > .studio-thesis-board")
    expect(board).to_be_visible()
    assert board.evaluate(
        "node => getComputedStyle(node).gridTemplateColumns.split(' ').length"
    ) == 3
    expect(
        project.locator(".research-hold").filter(
            has_text=re.compile(r"prerequisites satisfied", re.I)
        )
    ).to_have_count(0)


def verify_investigation_navigation(page, fixture, root, expect):
    expect(page.locator("#nav-thesis")).to_have_count(0)
    page.locator("#activity-control").click()
    expect(page.locator("#project-aims-button")).to_be_visible()
    page.locator("#close-project-dialog").click()
    sidebar = page.get_by_role("region", name="Investigations", exact=True)
    expect(
        sidebar.get_by_role("button", name=re.compile("New investigation"))
    ).to_be_visible()
    record = fixture["matrix_investigation"]
    sidebar.get_by_role(
        "button", name=re.compile(re.escape(record["question"]))
    ).click()
    tabs = page.get_by_role("tablist", name="Investigation stages")
    expect(tabs.get_by_role("tab")).to_have_count(3)
    expect(tabs.get_by_role("tab", name="Define an aim")).to_be_visible()
    plan = tabs.get_by_role("tab", name="Plan & run", exact=True)
    findings = tabs.get_by_role("tab", name="Review findings", exact=True)
    findings.click()
    expect(findings).to_have_attribute("aria-selected", "true")
    expect(page.locator("#results")).to_be_visible()
    expect(page.locator("#canvas-detail")).to_be_hidden()
    for panel in ("review", "provenance", "execution", "environment"):
        expect(page.locator("#canvas-detail #panel-" + panel)).to_be_hidden()
    expect(page.locator("#wc-stage-content-3")).to_be_visible()
    expect(
        page.get_by_role("img", name="Verified batch diagnostic figure")
    ).to_be_visible()
    page.wait_for_function(
        """() => document.querySelector(
            'img[alt="Verified batch diagnostic figure"]'
        )?.naturalWidth > 0"""
    )
    screenshot(page, findings, root, "investigation-review-findings.png")
    page.locator("#open-command-palette-btn").click()
    search = page.locator("#workspace-search")
    expect(search).to_be_focused()
    search.fill("expression")
    search.fill("")
    page.keyboard.press("Escape")
    expect(search).to_be_hidden()
    expect(findings).to_have_attribute("aria-selected", "true")
    expect(page.locator("#results")).to_be_visible()
    plan.click()
    expect(plan).to_have_attribute("aria-selected", "true")
    expect(page.locator("#wc-stage-content-2")).to_be_visible()
    expect(page.locator("#wc-stage-content-3")).to_be_hidden()


def verify_queue(page, panel, fixture, root, expect):
    form = section(panel, "Queue single-cell step")
    form.get_by_role(
        "combobox", name=re.compile(r"^Single-cell operation")
    ).select_option("scrna_batch_qc")
    form.get_by_label("Step ID", exact=True).fill("browser-qc")
    form.get_by_role("combobox", name=re.compile(r"^expression source")).select_option(
        label="count-source"
    )
    form.get_by_label("batch key", exact=True).fill("batch")
    grant = form.get_by_label("Allow data execution for this workflow change")
    expect(grant).not_to_be_checked()
    grant.check()
    endpoint = "/api/scientific/single-cell/" + fixture["session_id"]
    response = post(
        page,
        endpoint,
        form.get_by_role("button", name="Queue single-cell step", exact=True),
    )
    assert response["step_id"] == "browser-qc"
    queued = item(panel, "Step browser-qc")
    expect(queued).to_have_attribute("data-status", "ready")
    screenshot(page, queued, root, "single-cell-queued.png")
    return response


def open_research(page, origin, fixture, expect):
    page.goto(origin + "/?" + urlencode(fixture["analysis_ref"]))
    page.wait_for_load_state("networkidle")
    page.get_by_label("Retained planning session").select_option(fixture["session_id"])
    panel = page.locator("#scientific-adaptive-panel")
    expect(panel.locator('[data-workflow-band="execution"]')).to_be_visible()
    expect(page.locator("#wc-stage-bar")).to_be_hidden()
    return panel


def verify_workflow_bands(panel, expect):
    for band in ("design", "execution", "package"):
        expect(panel.locator(f'[data-workflow-band="{band}"]')).to_be_visible()
    expect(panel.get_by_role("tab", name="Thesis", exact=True)).to_have_count(0)


def record_response(response, origin, report):
    url = urlsplit(response.url)
    if url.scheme in {"blob", "data"}:
        return
    request = response.request
    event = {
        "method": request.method,
        "path": url.path,
        "status": response.status,
    }
    if not response.url.startswith(origin + "/"):
        event["external"] = True
    if request.method == "POST" and re.match(
        r"/api/scientific/(adaptive|single-cell|thesis)/", event["path"]
    ):
        body = request.post_data_json
        event["action"] = body.get("action")
        event["revision_supplied"] = type(body.get("expected_revision")) is int
    report["requests"].append(event)


def assert_http_responses(report, session_id):
    expected_hold = {
        "method": "POST",
        "path": f"/api/scientific/thesis/{session_id}",
        "status": 409,
        "action": "record_outcome",
        "revision_supplied": True,
    }
    requests = report["requests"]
    assert requests.count(expected_hold) == 1, requests
    unexpected = [
        entry
        for entry in requests
        if entry.get("external")
        or entry.get("revision_supplied") is False
        or entry["path"] == "/api/scientific/thesis/null"
        or (entry["status"] >= 400 and entry != expected_hold)
    ]
    report["unexpected_http_responses"] = unexpected
    assert not unexpected, unexpected


def run_browser(root, fixture):
    from playwright.sync_api import expect, sync_playwright

    origin = f"http://127.0.0.1:{fixture['port']}"
    report = {"scope": "Synthetic Workbench software verification", "requests": []}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1080})
        page.set_default_timeout(12_000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        page.on("response", lambda response: record_response(response, origin, report))
        try:
            page.goto(origin + "/")
            page.wait_for_load_state("networkidle")
            verify_investigation_navigation(page, fixture, root, expect)
            verify_appearance(page, root, expect)
            panel = open_research(page, origin, fixture, expect)
            verify_lineage(page, panel, root, expect)
            verify_workflow_bands(panel, expect)
            report["chromium"] = browser.version
            report["qc_revision"] = verify_qc(page, panel, fixture, root, expect)
            adaptive = "/api/scientific/adaptive/" + fixture["session_id"]
            report["step_dispatches"] = [
                advance(page, panel, adaptive, expect) for _ in range(3)
            ]
            assert report["step_dispatches"] == [1, 1, 0], report["step_dispatches"]
            page.locator("#activity-control").click()
            page.locator("#project-aims-button").click()
            project = page.locator("#project-aims")
            verify_thesis_board(project, expect)
            review = project.locator("section.thesis-aim-group").filter(
                has=page.get_by_role("heading", name="Needs review", exact=True)
            )
            review.get_by_role("button", name="Open aim " + fixture["aim_id"]).click()
            project_panel = page.locator("#investigation-adaptive-panel")
            report["thesis"] = verify_thesis(
                page,
                project_panel,
                fixture,
                root,
                expect,
            )
            page.locator("#activity-control").click()
            page.locator("#project-aims-button").click()
            included = project.locator("section.thesis-aim-group").filter(
                has=page.get_by_role("heading", name="Ready to include", exact=True)
            )
            expect(
                included.get_by_role("button", name="Open aim " + fixture["aim_id"])
            ).to_be_visible()
            needs_information = project.locator("section.thesis-aim-group").filter(
                has=page.get_by_role("heading", name="Needs information", exact=True)
            )
            expect(
                needs_information.get_by_role(
                    "button", name="Open aim " + fixture["new_aim"]["aim_id"]
                )
            ).to_be_visible()
            verify_thesis_matrix(page, project, fixture, root, expect)
            panel = open_research(page, origin, fixture, expect)
            file_count = verify_export(
                page,
                panel,
                root,
                report["thesis"]["package"],
                "inline-thesis-evidence.zip",
            )
            assert file_count == report["thesis"]["verified_export"]
            report["thesis"]["verified_inline_export"] = file_count
            report["queued"] = verify_queue(page, panel, fixture, root, expect)
            stale = panel.locator('.research-item[data-status="stale"]')
            expect(stale.get_by_text(re.compile("research package"))).to_be_visible()
            expect(
                stale.get_by_role("button", name="Export research package (.zip)")
            ).to_have_count(0)
            report["stale_export_withheld"] = True
            link = urlencode({"session_id": fixture["session_id"], "view": "workflows"})
            page.goto(origin + "/?" + link)
            expect(page.locator("#canvas-workflows")).to_have_attribute(
                "aria-selected", "true",
            )
            page.wait_for_load_state("networkidle")
            assert_http_responses(report, fixture["session_id"])
            assert not errors, errors
            report["status"] = "passed"
        except Exception as error:
            report["status"] = "failed"
            report["error"] = f"{type(error).__name__}: {error}"[:2000]
            with suppress(Exception):
                page.screenshot(path=root / "browser-failure.png", timeout=3_000)
            with suppress(Exception):
                (root / "browser-failure.txt").write_text(
                    page.locator("body").inner_text(timeout=3_000)[:12_000]
                )
            raise
        finally:
            browser.close()
            (root / "browser-verification.json").write_text(
                json.dumps(report, indent=2) + "\n"
            )
    print(f"Chromium {report['chromium']} verified the synthetic research flows.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        required=True,
        help="Fresh absolute evidence directory in the checkout",
    )
    parser.add_argument(
        "--image", required=True, help="Local pinned single-cell kernel image digest"
    )
    parser.add_argument(
        "--runtime-python",
        default=sys.executable,
        help="Python for the Workbench server",
    )
    parser.add_argument("--fixture-server", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if (
        args.root != args.root.resolve()
        or not args.root.is_relative_to(PROJECT)
        or args.root == PROJECT
    ):
        parser.error("Use an absolute evidence directory inside the checkout")
    if args.fixture_server:
        sys.path.insert(0, str(PROJECT))
        from tests.support.workbench_research_browser_fixture import research_fixture

        stopping = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stopping.set())
        with research_fixture(args.root, args.image):
            stopping.wait()
    else:
        args.root.mkdir(mode=0o700)
        with local_server(args.root, args.runtime_python, args.image) as fixture:
            run_browser(args.root, fixture)


if __name__ == "__main__":
    main()

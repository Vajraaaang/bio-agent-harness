# Scientific figure performance observations

The previous UI displays a static Matplotlib PNG. The redesigned UI adds an interactive Canvas2D view of verified saved coordinates. Static decode and interactive mount perform different tasks; these measurements do **not** establish a speedup or scientific benefit.

Browser observations were made in the in-app Chromium browser, Chrome 156 / macOS-reported user agent, 1280×720 CSS pixels at DPR 2. Hardware was not independently inventoried. Each row below is one observed workload, with no warm/cold statistical claim. Full raw runs and timestamps are retained in `reports/workbench-redesign/2026-10-28/performance/browser-observations.json`. Data is deterministic synthetic geometry, not biological UMAP or inference.

| Measurement | 50,000 points | 100,000 points |
| --- | ---: | ---: |
| JSON transfer size | 7,560,739 bytes | 15,169,004 bytes |
| Fetch and parse | 47.8 ms | 87.6 ms |
| Original 960×600 PNG decode | 8.8 ms | 7.4 ms |
| Interactive mount + first draw scheduling | 39.9 ms | 64.6 ms |
| Redraw including scheduling | 25.1 ms | 27.5 ms |
| Last measured drawing work | 21.7 ms | 21.2 ms |
| Exact polygon selection | 2.6 ms | 5.7 ms |
| Expected and observed selected IDs | 25,000 / 25,000 | 50,000 / 50,000 |
| Exact selected-identity equality | PASS | PASS |
| Full saved-table CSV serialization | 28.9 ms | 249.9 ms |

Both workloads rendered all saved points without downsampling. The exact-selection measurement executes the actual module's polygon geometry helper in the browser; separate UI checks exercise two exact-ID table selections and their question handoff in the real app. These are distinct checks, not proof of sustained lasso/pointer responsiveness.

The 100k fixture is below the existing 16 MiB figure-payload ceiling. Native display budgets further bound population to 200k and cumulative rows to 400k; these are safety ceilings, not validated smoothness promises at every allowed size. Forty-row pagination bounds table DOM. One active renderer and generation-checked lazy data loads avoid accumulating figures and accepting stale responses.

CSV serialization at 100k took ~250 ms and can cause a short main-thread pause. Sustained FPS, browser heap profiles, long-session leakage, large SVG/PNG export times, repeated resize/recolor distributions, million-cell WebGL rendering, mobile performance and real biological dataset stress remain unmeasured. Native Canvas is not GPU point-cloud rendering. A worker or a reviewed Plotly/deck.gl adapter should be considered if measured operational workloads require it.

Reproduce with the main-guarded `scripts/benchmark_workbench_figures.py --serve`, then use the clearly labeled benchmark page's load, image, mount, redraw, selection, CSV and save controls. The harness is separate software-test evidence and never populates production analysis results.

# Proposed external evaluation scope

Status: awaiting destination-specific authorization. No live Codex or Claude benchmark turn or terminal source-review prompt has been executed for this expansion.

## Source review destination

Anthropic, through the existing authenticated Claude CLI Team session. The proposed review is read-only and concerns the platform source and architecture: statistical modeling, guarded dispatch, execution adapters, graders, provenance, replay, benchmark definitions and the Part 11 control map. No host credentials, files outside this project, or private clinical/patient data are in scope. The reviewer is not asked to modify files or use external connectors.

## Live benchmark destinations

OpenAI via the local ChatGPT-authenticated Codex CLI and Anthropic via the local Claude subscription CLI. The initial proposed pilot uses one task from each of the four synthetic tracks, two assistants, and both baseline and assisted conditions: 16 runs, each with up to four lifecycle phases and a 300-second total deadline. Exact model identifiers and missing usage will be reported as observed. No API-key-funded fallback is enabled.

The broader approved data scope, if granted, would also allow the already-public mouse bulk subset. It excludes private clinical inputs. The full 55-task synthetic comparison is available as a separate 220-run command and should be chosen with subscription quota in mind.

## Measurements

Outcomes are deterministic task grades, tool/diagnostic behavior, complete or explicitly missing usage, sampled memory, and paired comparisons with uncertainty and coverage. No predetermined performance gain is required. Observable summaries are retained; private reasoning is not requested or archived. Results are research evidence and cannot release a clinical biomarker.

## Why execution is paused

Automatic approval review rejected the attempted terminal Claude source review because it required explicit permission to export repository source and architecture to Anthropic. The pending approval question also covers public/synthetic benchmark inputs sent to OpenAI and Anthropic. Local development, tests, public-data numerical analysis and audit verification continue independently.

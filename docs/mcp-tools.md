# MCP tool reference

`devbench mcp` connects a STDIO client to the daemon for its workspace. Use `tools/list` for the 13 live input and output schemas. Calls return bounded text and structured content. A client can complete a reviewed run using these tools and the local review handoff. [Client setup](quickstart.md) · [Research workflow](../.agents/skills/rna-research-core/SKILL.md)

| Tool | When to call it |
| --- | --- |
| `inspect_runtime_capabilities` | Check patched WAL, installed packages, eligible images and available worker slots. |
| `inspect_inputs` | Inspect bounded measurements and file metadata for declared public or synthetic inputs. |
| `register_investigation_input` | Bind an input, its representation and source declaration to an investigation. Preserve the returned session, revision and artifact identity. |
| `check_answerability` | Assess biological units, design rank and estimability for the proposed question. |
| `list_kernels_and_methods` | Look up methods, their supported representations, runtime availability and calibration scope. |
| `list_investigation_state` | Resume from a session ID and inspect its aims, artifacts, runs and review holds. |
| `manage_research_aim` | List or revise aims, manage branches, request Critic review or build a package. Bind mutations to the current revision. |
| `freeze_analysis_plan` | Record the inputs, settings, execution lane and objective. Keep the returned plan ID and digest for review. |
| `submit_reviewed_run` | Submit an eligible frozen plan with its session ID, plan ID and exact digest. Use the returned handoff when review is required. |
| `get_run_status` | Poll a run using its session and run IDs. A disconnected client can resume polling with the same identifiers. |
| `cancel_run` | Request cancellation with the session, run ID and reason. Check the reported cleanup state. |
| `get_run_receipt` | Retrieve terminal evidence, limitations and verified output identities. |
| `query_artifact_table` | Project selected columns or aggregates using the session, artifact ID and SHA-256. Follow `next_offset` for another page. |

## Inspect a public synthetic counts file

Send these arguments to `inspect_inputs` after setting the input root to the directory containing the fixture.

```json
{"inputs":[{"path":"counts.csv","file_kind":"expression","data_classification":"synthetic"}]}
```

## Ask about the design

Send the following arguments to `check_answerability` for an initial assessment. For a study-specific result, register the metadata first and bind the design to its artifact and verified donor identities.

```json
{"question_text":"Is the treated versus control contrast estimable across donors?","assay":"bulk_rna_seq","entry_point":"count_matrix"}
```

Private and clinical inputs stay in local offline Studio. Use opaque identifiers and the supplied handoff for a protected investigation. [Private-data workflow](../.agents/skills/private-data-studio/SKILL.md)

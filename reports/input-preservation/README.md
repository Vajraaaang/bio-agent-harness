# Controlled input-preservation checks

Version **0.4.13** enforces configured byte and member limits while hashing and copying controlled inputs. It also reserves directory destinations exclusively. The original finite checks reproduced a 16-byte cap retaining 33 bytes before rejection and a 10,000-entry cap admitting 10,001 entries after the preflight inventory changed. A separate review exposed and corrected two concurrent copies merging into one destination.

The full fresh local suite passed **1,930 tests with two platform-specific skips**; 23 installed-wheel tests passed. Four actual controlled analyses completed with exact repeat pairs and R-reference agreement. Twelve new/historical histories passed verification, including eight historical records. These are software-control checks, not clinical or live-agent performance results.

The [summary](summary.json) records counts and scope. Existing artifact encodings, scientific procedures, 61 task definitions and 83 external dependency records were retained. Ordinary filesystem-operation latency and hostile-host isolation remain outside these bounds. Clinical admission/release and statistical qualification gaps remain unchanged.

The [Colab verification](../colab-verification/README.md) ran a separately frozen 0.4.12 source bundle; it does not validate this later change on Linux. Full local before/after source, failed tests, reviews and execution records remain in the source archive identified by the summary.

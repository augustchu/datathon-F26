# Possible next steps

**Recommended direction:** expand the project through **source-grounded field recovery**, **validated missing-field models**, and **targeted verification**. Together, these answer three different questions: what can we recover from evidence, what can we estimate reliably, and what should we verify next?

Prepared **21 November 2026, America/New_York**, after reviewing the original competition prompt, the prior project chat, project documentation and implementation, all 24 commits reachable from the GitHub branch at `f2a1e0e`, and the newer local model experiments. These are research proposals; this task did not train models or run an outreach campaign.

| Document | Contents |
| --- | --- |
| [Brainstorm and selection](BRAINSTORM.md) | Twelve candidate extensions, the selection rationale, trade-offs, and an implementation sequence |
| [Context and Git history](CONTEXT_AND_HISTORY.md) | Original prompt requirements, your evolving direction, current results and gaps, and every reviewed commit |
| [1. Source-grounded field recovery](01-source-grounded-field-recovery/README.md) | Recover missing facts from text and documents with exact branch attribution, citations, dates and scope |
| [2. Validated missing-field models](02-validated-missing-field-models/README.md) | Compare interpretable and ML estimates on fresh, relevant evaluations; quantify uncertainty and leave unsupported values unknown |
| [3. Targeted verification](03-targeted-verification/README.md) | Spend review effort on facts that could improve a recommendation, while collecting independent evaluation labels |

Each selected proposal begins with a summary and includes an academic literature review, project-specific justification, a bounded experiment, evaluation measures, limitations and adoption criteria. The literature search is targeted rather than a systematic review; primary publications and official guidance are linked beside the associated claims.

The strongest near-term demonstration is one service-and-location query whose answer improves because a missing fact was recovered or checked, with its original text visible. Model estimates should add clearly labeled context. A shared recommendation backend and a successful Databricks Free Edition run remain essential delivery work across all three directions.

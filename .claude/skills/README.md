# Project skills

Vendored from [mattpocock/skills](https://github.com/mattpocock/skills) (MIT —
see `LICENSE.upstream`). Copied rather than referenced so the repo is
self-contained; re-fetch from upstream to update.

| Skill | Invoked | What it is |
|---|---|---|
| `grill-with-docs` | `/grill-with-docs` | The entry point. A one-line delegation to the two below. |
| `grilling` | by delegation | The interview: a design tree worked in rounds, each question numbered and carrying a recommended answer. |
| `domain-modeling` | by delegation | The paper trail: resolved terms into `CONTEXT.md`, hard decisions into `docs/adr/`. |

**All three are required.** `grill-with-docs` does nothing on its own — its
`SKILL.md` is a single line telling the agent to load `grilling` and
`domain-modeling`. Upstream reports that partial loading is the most common
failure: a good interview with no files written means `domain-modeling` did not
load. Ask which skills were loaded if the session produces no `CONTEXT.md`.

Nothing is scaffolded up front. `CONTEXT.md` appears at the repo root the moment
the first term resolves; `docs/adr/` when the first decision clears all three
gates (hard to reverse, surprising without context, a real trade-off). Most
sessions produce no ADRs, which is the intended behaviour rather than a fault.

`CONTEXT.md` is a glossary and only a glossary — no implementation detail, no
spec. Decisions that do not earn an ADR live in the conversation and nowhere
else, so keep the session rather than clearing it.

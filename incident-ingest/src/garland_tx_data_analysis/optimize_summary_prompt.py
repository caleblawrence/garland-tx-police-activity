"""Evolve the monthly-summary prompt against the checks that already gate it.

`monthly_summary` writes a paragraph and then refuses to store it if any
numeral in it is unaccounted for by the figures it was given. Today a refusal
is a discarded run: nothing learns from it. This points GEPA at that same
check, plus the rest of the prompt's stated rules, and lets the rejections
improve the prompt instead of being thrown away.

GEPA is used directly — the `gepa` package, no DSPy. DSPy ships `dspy.GEPA` as
a wrapper for DSPy programs; the library underneath takes any system whose
prompts you can name, so the adapter here runs `monthly_summary.generate`, the
same call the pipeline makes, with the candidate prompt substituted.

Two models, doing different jobs:

  - the task model is Haiku, unchanged, because it is what actually writes
    these and a prompt tuned against a stronger model would be tuned against
    the wrong reader.
  - the reflection model is Opus, which reads the failures and proposes the
    next prompt. It is called perhaps thirty times a run, against several
    hundred Haiku calls, so the capable model is affordable exactly where it
    matters.

Nothing is written to the database and nothing is written to
`monthly_summary.py`. A run leaves its best prompt in a file for a person to
read, diff and paste in deliberately, which is the same bargain the rest of
this project makes with its model output.

    uv run python -m garland_tx_data_analysis.optimize_summary_prompt
    uv run python -m garland_tx_data_analysis.optimize_summary_prompt --optimize
    uv run python -m garland_tx_data_analysis.optimize_summary_prompt --optimize --budget 400

Needs DATABASE_URL for the first run, to build the trainset from the months
already ingested. After that the trainset is cached and the optimiser runs
without a database.
"""

import argparse
import json
import os
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv

from garland_tx_data_analysis.agent import LABEL_MODEL
from garland_tx_data_analysis.monthly_summary import (
    SYSTEM_PROMPT,
    generate,
    stats_for,
)
from garland_tx_data_analysis.summary_metric import evaluate_summary
from garland_tx_data_analysis.tools import WORK_DIR, connect, ensure_schema

# The reflection model reads failures and rewrites the prompt. Opus by default:
# it is the small half of the token bill here and the half that decides whether
# the run produces anything worth reading.
REFLECTION_MODEL = os.getenv("GARLAND_REFLECTION_MODEL", "anthropic:claude-opus-5")

# The component GEPA evolves. One named prompt, matching the one real prompt.
COMPONENT = "monthly_summary_system_prompt"

TRAINSET_PATH = Path(WORK_DIR) / "summary_trainset.json"
RUN_DIR = Path(WORK_DIR) / "gepa_summary"

# Every month is one Haiku call, and the months are independent, so the wall
# clock is worth spending threads on. Modest, to stay clear of rate limits.
CONCURRENCY = 6

# Every third month, deterministically. A seeded shuffle would split just as
# well, but a fixed stride keeps the two sets spread evenly across four years
# rather than clustering a season in one of them.
VAL_STRIDE = 3


def build_trainset(refresh: bool = False) -> list[dict]:
    """The stats block for every ingested month, cached to disk.

    Cached because it is the one part of this that needs Postgres, it does not
    change between optimisation runs, and re-deriving it turns every
    experiment into a database round trip for no reason.
    """
    if TRAINSET_PATH.exists() and not refresh:
        return json.loads(TRAINSET_PATH.read_text())

    with connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT report_month FROM monthly_reports ORDER BY report_month"
            )
            months = [r[0] for r in cur.fetchall()]

    examples = [{"month": m, "stats": stats_for(m)} for m in months]
    TRAINSET_PATH.parent.mkdir(parents=True, exist_ok=True)
    TRAINSET_PATH.write_text(json.dumps(examples, indent=1, default=str))
    return examples


def split(examples: list[dict]) -> tuple[list[dict], list[dict]]:
    val = examples[::VAL_STRIDE]
    train = [e for i, e in enumerate(examples) if i % VAL_STRIDE]
    return train, val


def run_prompt(prompt: str, examples: list[dict], model) -> list[dict]:
    """Write a summary for every example, and score each one."""

    def one(example: dict) -> dict:
        stats = example["stats"]
        try:
            text = generate(stats, system_prompt=prompt, model=model)
        except Exception as exc:  # noqa: BLE001 - one bad month must not end a run
            return {
                "month": example["month"],
                "stats": stats,
                "summary": "",
                "score": 0.0,
                "publishable": False,
                "feedback": f"The call failed: {exc}",
            }
        result = evaluate_summary(text, stats)
        return {
            "month": example["month"],
            "stats": stats,
            "summary": text,
            "score": result.score,
            "publishable": result.publishable,
            "feedback": result.feedback,
        }

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        return list(pool.map(one, examples))


class SummaryAdapter:
    """GEPA's view of the monthly-summary step.

    `evaluate` runs a candidate prompt and scores it; `make_reflective_dataset`
    hands the reflection model the failures in the shape it reads. The protocol
    is structural, so this is a plain class rather than a subclass — it only has
    to have the two methods.
    """

    # GEPA reads this attribute directly to decide whether the adapter
    # proposes its own prompt rewrites. `None` means use GEPA's own proposer,
    # which is what we want — but the engine does not tolerate its absence, and
    # every reflection attempt fails silently without it, burning the whole
    # budget on a run that never proposes anything.
    propose_new_texts = None

    def __init__(self, model):
        self.model = model

    def evaluate(self, batch, candidate, capture_traces: bool = False):
        from gepa import EvaluationBatch

        prompt = candidate[COMPONENT]
        results = run_prompt(prompt, batch, self.model)
        return EvaluationBatch(
            outputs=[r["summary"] for r in results],
            scores=[r["score"] for r in results],
            trajectories=results if capture_traces else None,
        )

    def make_reflective_dataset(self, candidate, eval_batch, components_to_update):
        # The figures block goes in verbatim. Most of what goes wrong here is
        # a number that is not in it, and a reflection model that cannot see
        # the block can only take the feedback's word for which number that was.
        records = [
            {
                "Inputs": {"figures_block": json.dumps(t["stats"], indent=1)},
                "Generated Outputs": t["summary"] or "(nothing)",
                "Feedback": t["feedback"],
            }
            for t in eval_batch.trajectories
        ]
        return {name: records for name in components_to_update}


def reflector():
    """The reflection model, as the plain callable GEPA asks for.

    Routed through `init_chat_model` like every other model in this project,
    which also keeps the optimiser off a second SDK. `max_tokens` is raised
    because the thing being generated is a whole system prompt, and the
    default would truncate one silently.
    """
    from langchain.chat_models import init_chat_model

    model = init_chat_model(REFLECTION_MODEL, max_tokens=8000)

    def ask(prompt) -> str:
        messages = prompt if isinstance(prompt, list) else [("user", prompt)]
        reply = model.invoke(messages).content
        if isinstance(reply, list):
            reply = " ".join(
                p.get("text", "") for p in reply if isinstance(p, dict)
            )
        return str(reply)

    return ask


def report(label: str, results: list[dict]) -> float:
    published = sum(1 for r in results if r["publishable"])
    mean = statistics.mean(r["score"] for r in results) if results else 0.0
    print(f"\n{label}")
    print(f"  publishable  {published}/{len(results)}")
    print(f"  mean score   {mean:.3f}")
    for r in results:
        if not r["publishable"]:
            first = r["feedback"].split("\n")[1].strip(" -") if "\n" in r["feedback"] else r["feedback"]
            print(f"    {r['month']}  REJECTED — {first[:96]}")
    return mean


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--optimize", action="store_true",
                        help="evolve the prompt; without it, only the current one is scored")
    parser.add_argument("--budget", type=int, default=300,
                        help="max summary generations GEPA may spend (default 300)")
    parser.add_argument("--refresh", action="store_true",
                        help="rebuild the trainset from the database")
    args = parser.parse_args(argv)

    from langchain.chat_models import init_chat_model

    examples = build_trainset(refresh=args.refresh)
    train, val = split(examples)
    print(f"{len(examples)} months  —  {len(train)} train, {len(val)} validation")

    model = init_chat_model(LABEL_MODEL)
    baseline = run_prompt(SYSTEM_PROMPT, val, model)
    baseline_mean = report(f"Current prompt on the validation months ({LABEL_MODEL})",
                           baseline)

    if not args.optimize:
        print("\nScored the current prompt only. Re-run with --optimize to evolve it.")
        return

    import gepa

    RUN_DIR.mkdir(parents=True, exist_ok=True)
    print(f"\nOptimising with {REFLECTION_MODEL} reflecting, "
          f"budget {args.budget} generations. This costs real money.")

    result = gepa.optimize(
        seed_candidate={COMPONENT: SYSTEM_PROMPT},
        trainset=train,
        valset=val,
        adapter=SummaryAdapter(model),
        reflection_lm=reflector(),
        max_metric_calls=args.budget,
        run_dir=str(RUN_DIR),
        display_progress_bar=True,
        seed=0,
    )

    best = result.best_candidate[COMPONENT]
    out = RUN_DIR / "best_prompt.txt"
    out.write_text(best)

    evolved = run_prompt(best, val, model)
    evolved_mean = report("Evolved prompt on the same validation months", evolved)

    print(f"\nmean score  {baseline_mean:.3f} -> {evolved_mean:.3f}")
    print(f"best prompt written to {out}")
    print("Nothing was stored and monthly_summary.py was not touched. Read the "
          "prompt, and paste it in yourself if it earns it.")


if __name__ == "__main__":
    load_dotenv(override=True)
    main(sys.argv[1:])

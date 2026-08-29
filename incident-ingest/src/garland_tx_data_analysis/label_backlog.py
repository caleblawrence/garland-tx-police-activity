#!/usr/bin/env python
"""Give a plain-English label to every offence code that lacks one.

The weekly pipeline labels codes as it meets them, so the 68 it has seen read
`Vehicle Burglary`. The monthly archive brought 189 codes, and the other 121
had never been through it — so the archive page showed
`THEFT-ALL OTHER-TWO OR MORE PREVIOUS CONVICTIONS L/T $2500` beside
`Vehicle Burglary` and the two looked like duplicates of each other.

This runs the same labeller over the backlog and writes through the same
`ON CONFLICT DO NOTHING` path, so a code that already has a label keeps it.
Nothing here can change a name the weekly pipeline or the backfill decided.

    uv run python -m garland_tx_data_analysis.label_backlog          # report
    uv run python -m garland_tx_data_analysis.label_backlog --apply
"""

import json
import sys

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model

from garland_tx_data_analysis.agent import LABEL_MODEL, OFFENCE_LABELLER
from garland_tx_data_analysis.tools import _learn_labels, connect, ensure_schema

# Small enough that the model keeps every key verbatim, which matters: a key
# that comes back altered silently fails to match and the code stays unlabelled.
BATCH = 25


def unlabelled() -> list[str]:
    with connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT m.incident
                  FROM monthly_incidents m
                  LEFT JOIN incident_labels l USING (incident)
                 WHERE l.incident IS NULL
                 ORDER BY 1
                """
            )
            return [r[0] for r in cur.fetchall()]


def label(codes: list[str]) -> dict[str, str]:
    model = init_chat_model(LABEL_MODEL)
    mapping: dict[str, str] = {}
    for i in range(0, len(codes), BATCH):
        batch = codes[i : i + BATCH]
        reply = model.invoke(
            [
                ("system", OFFENCE_LABELLER["system_prompt"]),
                ("user", json.dumps(batch, indent=1)),
            ]
        )
        text = reply.content
        if isinstance(text, list):
            text = " ".join(p.get("text", "") for p in text if isinstance(p, dict))
        text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
        try:
            got = json.loads(text)
        except json.JSONDecodeError:
            print(f"  batch {i // BATCH + 1}: unparseable reply, skipped", file=sys.stderr)
            continue
        missing = [c for c in batch if c not in got]
        if missing:
            print(f"  batch {i // BATCH + 1}: {len(missing)} code(s) not returned")
        mapping.update({k: v for k, v in got.items() if k in batch and v})
        print(f"  batch {i // BATCH + 1}: {len(got)} of {len(batch)} labelled")
    return mapping


def main(apply: bool = False) -> None:
    codes = unlabelled()
    print(f"offence codes with no label: {len(codes)}")
    if not codes:
        return
    if not apply:
        for c in codes[:10]:
            print(f"   {c}")
        print(f"\nDry run. Re-run with --apply to label them with {LABEL_MODEL}.")
        return

    mapping = label(codes)
    with connect() as conn:
        ensure_schema(conn)
        learned, ignored = _learn_labels(conn, mapping)
    print(f"\nlearned {len(learned)} new labels; {len(ignored)} ignored as already named")
    still = unlabelled()
    print(f"still unlabelled: {len(still)}{' — ' + ', '.join(still[:5]) if still else ''}")


if __name__ == "__main__":
    load_dotenv(override=True)
    main(apply="--apply" in sys.argv)

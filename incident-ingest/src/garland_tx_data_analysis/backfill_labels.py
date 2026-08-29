#!/usr/bin/env python
"""One-shot: settle every offence code on a single label.

Labels used to be re-derived by the model on every run, so the same code was
named differently from week to week — 68 codes had accumulated 94 labels, and
398 of 610 rows carried a code that had drifted. `incident_labels` stops that
happening again; this script cleans up what it already produced.

The mapping below is explicit rather than regenerated, because regenerating is
what caused the problem. It was built by one rule: a code whose label never
drifted keeps that label verbatim, and a code that drifted was decided by hand
using the labeller's own conventions — no dollar thresholds, keep the elements
of the offence (attempt, weapon, information report), title case, short.

Two labels that had not drifted are overridden anyway, because they were
inconsistent with the family they belong to:

    INFO-BURGLARY                                  Burglary -> Burglary Report
    THEFT-MOTOR VEH PARTS/ACCESSORIES-$2,500 ...   dropped the dollar range

Dry by default; pass --apply to write.

    uv run python -m garland_tx_data_analysis.backfill_labels
    uv run python -m garland_tx_data_analysis.backfill_labels --apply

Like the TinyDB import before it, this is disposable — delete it once it has
run against the live database.
"""

import sys

from dotenv import load_dotenv

from garland_tx_data_analysis.tools import connect, ensure_schema

CANONICAL_LABELS = {
    'ASSAULT-AGG-D/W': 'Aggravated Assault with Deadly Weapon',
    'ASSAULT-AGG-SBI': 'Aggravated Assault',
    'BURGLARY-BLDG': 'Building Burglary',
    'BURGLARY-BLDG-(CRIM ATT)': 'Attempted Building Burglary',
    'BURGLARY-HAB': 'Habitation Burglary',
    'BURGLARY-HAB W/INT TO COMMIT ASSLT-BI OR THREAT': 'Habitation Burglary with Assault Intent',
    'BURGLARY-HAB W/INT TO COMMIT SEX ASSLT': 'Habitation Burglary with Sexual Assault Intent',
    'BURGLARY-HAB-(CRIM ATT)': 'Attempted Habitation Burglary',
    'BURGLARY-VEH': 'Vehicle Burglary',
    'BURGLARY-VEH-(CRIM ATT)': 'Attempted Vehicle Burglary',
    'BURGLARY-VEH-ATTACHED PARTS OR ACCESSORIES': 'Vehicle Parts Burglary',
    'CRIMINAL MISCHIEF $100 L/T $750': 'Vandalism',
    'CRIMINAL MISCHIEF $2,500 L/T $30,000': 'Vandalism',
    'CRIMINAL MISCHIEF $750 L/T $2,500': 'Vandalism',
    'CRIMINAL MISCHIEF L/T $100': 'Vandalism',
    'CRIMINAL MISCHIEF-DESTROY SCHOOL-$750 L/T $30,000': 'School Vandalism',
    'CRIMINAL MISCHIEF-IMPAIR PUB SERV L/T $30,000': 'Vandalism',
    'INFO-AUTO THEFT ABANDONED VEHICLE': 'Abandoned Vehicle Report',
    'INFO-BURGLARY': 'Burglary Report',
    'INFO-FOUND PROPERTY-THEFTS': 'Found Property Report',
    'INFO-IDENTITY THEFT': 'Identity Theft Report',
    'INFO-THEFT': 'Theft Report',
    'INFO-THEFT/MAIL/FRAUD-INFORMATION REPORT ONLY': 'Mail Fraud Report',
    'INFO-VEH THEFT': 'Vehicle Theft Report',
    'INJURY TO A CHILD-BI': 'Injury to a Child',
    'INJURY TO AN ELDERLY PERSON-BI': 'Injury to an Elderly Person',
    'MURDER': 'Homicide',
    'OTHER AGENCY- THEFT-FIREARM': 'Firearm Theft',
    'OTHER AGENCY- UNAUTHORIZED USE MOTOR VEHICLE': 'Unauthorized Vehicle Use',
    'ROBBERY - AGG - BUSINESS': 'Aggravated Business Robbery',
    'ROBBERY - AGG - INDIV': 'Aggravated Robbery',
    'ROBBERY-BUSINESS': 'Business Robbery',
    'ROBBERY-INDIV': 'Robbery',
    'SEXUAL ASSLT AGG RAPE CHILD': 'Aggravated Child Sexual Assault',
    'SEXUAL ASSLT CHILD': 'Child Sexual Assault',
    'SEXUAL ASSLT-AGG-RAPE': 'Aggravated Sexual Assault',
    'SEXUAL ASSLT-RAPE': 'Sexual Assault',
    'SEXUAL ASSLT-RAPE-(CRIM ATT)': 'Attempted Rape',
    'THEFT BY CHECK-$750 L/T $2,500': 'Theft by Check',
    'THEFT CATALYTIC CONVERTER <30K TO REPLACE': 'Catalytic Converter Theft',
    'THEFT OF SERVICE >=$750 < $2,500': 'Theft of Service',
    'THEFT OF SERVICE-$100 L/T $750': 'Theft of Service',
    'THEFT OF SERVICE-$2,500 L/T $30,000': 'Theft of Service',
    'THEFT OF SERVICE-L/T $100': 'Service Theft',
    'THEFT PROP < $2,500 2 / MORE PREV CONV': 'Theft with Prior Convictions',
    'THEFT-ALL OTHER-$100 L/T $750': 'Theft',
    'THEFT-ALL OTHER-$2,500 L/T $30,000': 'Theft',
    'THEFT-ALL OTHER-$2,500 L/T $30,000-(CRIM ATT)': 'Attempted Theft',
    'THEFT-ALL OTHER-$30,000 L/T $150,000': 'Theft',
    'THEFT-ALL OTHER-$750 L/T $2,500': 'Theft',
    'THEFT-ALL OTHER-L/T $100': 'Theft',
    'THEFT-ALL OTHER-OVER $300,000': 'Theft',
    'THEFT-FIREARM': 'Firearm Theft',
    'THEFT-FROM PERSON-OTHER': 'Theft from Person',
    'THEFT-MAIL <10 ADDRESSES': 'Mail Theft',
    'THEFT-MOTOR VEH PARTS/ACCESSORIES-$100 L/T $750': 'Motor Vehicle Parts Theft',
    'THEFT-MOTOR VEH PARTS/ACCESSORIES-$2,500 L/T $30,000': 'Motor Vehicle Parts Theft',
    'THEFT-MOTOR VEH PARTS/ACCESSORIES-$750 L/T $2,500': 'Motor Vehicle Parts Theft',
    'THEFT-MOTOR VEH PARTS/ACCESSORIES-L/T $100': 'Motor Vehicle Parts Theft',
    'THEFT-MOTOR VEHICLE-$2,500 L/T $30,000': 'Motor Vehicle Theft',
    'THEFT-MOTOR VEHICLE-$30,000 L/T $150,000': 'Motor Vehicle Theft',
    'THEFT-MOTOR VEHICLE-L/T $2,500': 'Motor Vehicle Theft',
    'THEFT-SHOPLIFTING-$100 L/T $750': 'Shoplifting',
    'THEFT-SHOPLIFTING-$750 L/T $2,500': 'Shoplifting',
    'THEFT-SHOPLIFTING-L/T $100': 'Shoplifting',
    'THEFT-VEHICLE (NON-MOTOR VEH)-$2,500 L/T $30,000': 'Vehicle Theft',
    'UNAUTHORIZED USE MOTOR VEHICLE': 'Unauthorized Vehicle Use',
    'UNAUTHORIZED USE MOTOR VEHICLE-(CRIM ATT)': 'Attempted Unauthorized Vehicle Use',
}

COUNT_DRIFTED_SQL = """
    SELECT count(*) FROM (
        SELECT incident FROM incidents WHERE short_description IS NOT NULL
         GROUP BY 1 HAVING count(DISTINCT short_description) > 1) t
"""


def main(apply: bool = False) -> None:
    """Settle every code on its canonical label. Reads DATABASE_URL as set.

    Deliberately does NOT call load_dotenv: `override=True` would reload .env
    over whatever the caller had set, so a test pointed at the Neon `test`
    branch would rewrite the live database instead. The entrypoint loads .env;
    callers that set DATABASE_URL themselves keep it.
    """
    with connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT incident, count(*), count(DISTINCT short_description)
                  FROM incidents
                 WHERE short_description IS NOT NULL
                 GROUP BY 1
                """
            )
            current = {row[0]: (row[1], row[2]) for row in cur.fetchall()}

        drifted = sorted(code for code, (_, n) in current.items() if n > 1)
        uncovered = sorted(set(current) - set(CANONICAL_LABELS))
        rows_touched = sum(n for code, (n, _) in current.items() if code in CANONICAL_LABELS)

        print(f"codes in incidents:         {len(current)}")
        print(f"codes in canonical map:     {len(CANONICAL_LABELS)}")
        print(f"codes with a drifted label: {len(drifted)}")
        print(f"rows the update will set:   {rows_touched}")
        if uncovered:
            print(f"NOT COVERED (left alone):   {uncovered}")

        if not apply:
            print("\nDry run. Re-run with --apply to write.")
            return

        rows = sorted(CANONICAL_LABELS.items())
        with conn.cursor() as cur:
            # Deliberately an upsert. This script is the authority on what a
            # label should be, unlike the runtime path in store_incidents,
            # where a stored label always wins over a supplied one.
            cur.executemany(
                """
                INSERT INTO incident_labels (incident, short_description)
                VALUES (%s, %s)
                ON CONFLICT (incident)
                DO UPDATE SET short_description = EXCLUDED.short_description
                """,
                rows,
            )
            cur.executemany(
                """
                UPDATE incidents SET short_description = %s
                 WHERE incident = %s AND short_description IS DISTINCT FROM %s
                """,
                [(label, code, label) for code, label in rows],
            )

        with conn.cursor() as cur:
            cur.execute(COUNT_DRIFTED_SQL)
            still_drifted = cur.fetchone()[0]

    print(
        f"\nSeeded {len(rows)} labels. "
        f"Codes still carrying more than one label: {still_drifted}"
    )


if __name__ == "__main__":
    load_dotenv(override=True)
    main(apply="--apply" in sys.argv)

#!/usr/bin/env node
// Build the citywide trends page from the FBI's figures for Garland PD.
//
// Separate from the incident pages, and separate on purpose. These are UCR
// counts on a national standard; the incident browser is the city's own
// selected-incident list. They do not reconcile and are not meant to.

import { readFileSync, writeFileSync, mkdirSync, existsSync } from "fs";
import path from "path";
import { fileURLToPath } from "url";

const projectRoot = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const repoRoot = path.dirname(projectRoot);
const inputPath =
  process.env.UCR_JSON_PATH ||
  path.join(repoRoot, "incident-ingest", "work", "ucr_monthly.json");

if (!existsSync(inputPath)) {
  console.error(
    `UCR JSON not found at ${inputPath}\n` +
      `Run: cd incident-ingest && uv run python -m garland_tx_data_analysis.fbi_ucr --fetch --export`
  );
  process.exit(1);
}

const data = JSON.parse(readFileSync(inputPath, "utf-8"));

mkdirSync(path.join(projectRoot, "dist"), { recursive: true });
writeFileSync(path.join(projectRoot, "dist/ucr.json"), JSON.stringify(data));
writeFileSync(
  path.join(projectRoot, "dist/trends.html"),
  readFileSync(path.join(projectRoot, "src/trends.html"), "utf-8")
);

const months = [...new Set(data.rows.map((r) => r.month))].sort();
console.log(
  `trends: ${data.rows.length} rows, ${months.length} months ` +
    `(${months[0]} .. ${months[months.length - 1]}), ` +
    `${Object.keys(data.labels).length} categories`
);

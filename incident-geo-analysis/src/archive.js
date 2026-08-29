#!/usr/bin/env node
// Build the monthly archive page.
//
// Separate from index.js on purpose. The archive is a different dataset at a
// different grain — 53 monthly reports against the weekly feed's one week —
// and nothing here is geocoded or mapped. It is a list you can filter, which
// is all the archive needs to be and avoids putting 31,000 addresses through
// Nominatim.

import { readFileSync, writeFileSync, mkdirSync, existsSync } from "fs";
import path from "path";
import { fileURLToPath } from "url";

const projectRoot = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const repoRoot = path.dirname(projectRoot);
const inputPath =
  process.env.ARCHIVE_JSON_PATH ||
  path.join(repoRoot, "incident-ingest", "work", "archive_incidents.json");

if (!existsSync(inputPath)) {
  console.error(
    `Archive JSON not found at ${inputPath}\n` +
      `Run: cd incident-ingest && uv run python -m garland_tx_data_analysis.archive_ingest --export`
  );
  process.exit(1);
}

const data = JSON.parse(readFileSync(inputPath, "utf-8"));

// Intern the repeated strings. 31,000 rows share 189 offence codes and 36
// districts between them; spelling each one out per row made a 6.6MB file that
// a browser has to parse before it can show anything.
const codes = [];
const codeIndex = new Map();
const labels = [];
const districts = [];
const districtIndex = new Map();

const intern = (value, list, index) => {
  if (!index.has(value)) {
    index.set(value, list.length);
    list.push(value);
  }
  return index.get(value);
};

const rows = data.incidents.map((incident) => {
  const c = intern(incident.incident, codes, codeIndex);
  labels[c] = incident.short_description;
  const d = intern(incident.district ?? "", districts, districtIndex);
  return [incident.date, d, c, incident.location || ""];
});

const payload = {
  months: data.months,
  codes,
  labels,
  districts,
  rows,
  unlabelledCodes: data.unlabelled_codes,
};

mkdirSync(path.join(projectRoot, "dist"), { recursive: true });
const outJson = path.join(projectRoot, "dist/archive.json");
writeFileSync(outJson, JSON.stringify(payload));

const html = readFileSync(path.join(projectRoot, "src/archive.html"), "utf-8");
writeFileSync(path.join(projectRoot, "dist/archive.html"), html);

const kb = (p) => (readFileSync(p).length / 1024).toFixed(0);
console.log(
  `archive: ${rows.length} incidents across ${data.months.length} months, ` +
    `${codes.length} offence codes`
);
console.log(
  `  dist/archive.json  ${kb(outJson)} KB (from ${kb(inputPath)} KB raw)`
);
const short = data.months.filter((m) => m.shortfall_rows > 0);
if (short.length) {
  const total = short.reduce((sum, m) => sum + m.shortfall_rows, 0);
  console.log(
    `  ${short.length} month(s) incomplete, ${total} rows short — shown on the page`
  );
}

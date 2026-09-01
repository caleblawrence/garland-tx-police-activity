#!/usr/bin/env node
// Build the monthly archive page.
//
// Separate from index.js on purpose. The archive is a different dataset at a
// different grain — 53 monthly reports against the weekly feed's one week —
// and nothing here is geocoded or mapped. It is a list you can filter, which
// is all the archive needs to be and avoids putting 31,000 addresses through
// Nominatim.

import { readFileSync, writeFileSync, mkdirSync, existsSync, copyFileSync } from "fs";
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

// One category per code, not per row: the category is a property of the
// offence, so 31,000 rows do not each need to carry the string.
const codeCategory = [];

const rows = data.incidents.map((incident) => {
  const c = intern(incident.incident, codes, codeIndex);
  labels[c] = incident.short_description;
  codeCategory[c] = data.categories.indexOf(incident.category);
  const d = intern(incident.district ?? "", districts, districtIndex);
  return [incident.date, d, c, incident.location || ""];
});

const payload = {
  months: data.months,
  categories: data.categories,
  codes,
  labels,
  codeCategory,
  districts,
  rows,
  unlabelledCodes: data.unlabelled_codes,
};

mkdirSync(path.join(projectRoot, "dist"), { recursive: true });
const outJson = path.join(projectRoot, "dist/archive.json");
writeFileSync(outJson, JSON.stringify(payload));

// The featured news items, if any have been pinned. Absent is normal: ingest
// fills a pool, and nothing reaches the page until a person writes a title and
// summary for it by hand.
const newsPath =
  process.env.NEWS_JSON_PATH ||
  path.join(repoRoot, "incident-ingest", "work", "news_items.json");
const news = existsSync(newsPath)
  ? JSON.parse(readFileSync(newsPath, "utf-8"))
  : { items: [] };
writeFileSync(path.join(projectRoot, "dist/news.json"), JSON.stringify(news));

const html = readFileSync(path.join(projectRoot, "src/archive.html"), "utf-8");
writeFileSync(path.join(projectRoot, "dist/index.html"), html);

// archive.html was the front door for a day. Anyone who bookmarked or shared it
// in that window gets moved rather than a 404.
writeFileSync(
  path.join(projectRoot, "dist/archive.html"),
  '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">' +
    '<title>Moved</title><meta http-equiv="refresh" content="0; url=index.html">' +
    '<link rel="canonical" href="index.html"></head>' +
    '<body>This page is now <a href="index.html">the incident browser</a>.</body></html>'
);

// The city's own police district polygons, simplified from
// maps.garlandtx.gov/arcgis/rest/services/CityMap/Public_Safety (layer 4).
// Real boundaries rather than anything inferred from where incidents happen to
// have been geocoded — a choropleth drawn on guessed districts would look
// exactly as authoritative and be wrong.
copyFileSync(
  path.join(projectRoot, "src/police-districts.geojson"),
  path.join(projectRoot, "dist/police-districts.geojson")
);

const kb = (p) => (readFileSync(p).length / 1024).toFixed(0);
console.log(
  `news: ${news.items.length} featured item(s)` +
    (news.items.length ? "" : " — nothing pinned, the block will not render")
);
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

import { getFullAddress, hasBlockNumber, resolveBlockBox } from "./geo.js";
import {
  writeFileSync,
  readFileSync,
  mkdirSync,
  existsSync,
  copyFileSync,
} from "fs";
import ProgressBar from "progress";
import path from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const projectRoot = path.resolve(__dirname, "..");
const repoRoot = path.resolve(projectRoot, "..");

const STATIC_ASSETS = ["favicon.svg", "favicon-32.png", "apple-touch-icon.png"];

const AGENT_DATA_DIR = path.join(repoRoot, "incident-ingest");

// Prefer the enriched list (has short_description) and fall back to the raw
// extractor output if the formatter hasn't run yet.
const candidatePaths = process.env.INCIDENTS_JSON_PATH
  ? [path.resolve(process.env.INCIDENTS_JSON_PATH)]
  : [
      path.join(AGENT_DATA_DIR, "enriched_incidents.json"),
      path.join(AGENT_DATA_DIR, "extracted_incidents.json"),
    ];

const incidentsPath = candidatePaths.find((p) => existsSync(p));

if (!incidentsPath) {
  console.error(
    `Incidents JSON not found. Tried:\n${candidatePaths.map((p) => "  " + p).join("\n")}\n` +
      `Run the agent first (cd incident-ingest && uv run run_agent) ` +
      `or set INCIDENTS_JSON_PATH to point at a JSON file.`
  );
  process.exit(1);
}

const rawData = JSON.parse(readFileSync(incidentsPath, "utf-8"));

const normalizeIncidents = (data) => {
  // Accept either the flat list emitted by the agent or the legacy
  // {districtNumber: [incident, ...]} shape from the old scrape pipeline.
  const rows = Array.isArray(data) ? data : Object.values(data).flat();
  return rows
    .filter(
      (item) =>
        item &&
        typeof item === "object" &&
        "location" in item &&
        "incident" in item &&
        "date" in item
    )
    .map(
      (
        { date, incident, location, short_description, district, report_period },
        i
      ) => ({
        // Stable within a build, and the key the incident list uses to pan the
        // map to the matching box.
        id: i,
        date,
        incident,
        location,
        district,
        // Which week the city's report covers — the About page reads this.
        report_period,
        short_description: short_description || incident,
      })
    );
};

const processIncidents = async (data) => {
  const flatList = normalizeIncidents(data);

  const bar = new ProgressBar("  mapping [:bar] :percent :etas", {
    complete: "=",
    incomplete: " ",
    width: 20,
    total: flatList.length,
  });

  const geojsonFeatures = [];
  const confidentialAddresses = [];
  const unmappable = [];
  for (const item of flatList) {
    bar.tick();
    if (item.location === "ADDRESS CONFIDENTIAL") {
      confidentialAddresses.push(item);
      continue;
    }
    // Some rows come out of the PDF with a blank address column. Geocoding
    // those resolved to the Garland city centroid and drew a box downtown for
    // an incident that happened somewhere else entirely.
    if (!hasBlockNumber(item.location)) {
      unmappable.push(item);
      continue;
    }
    const bboxFeature = await resolveBlockBox(item.location);
    if (!bboxFeature) {
      unmappable.push(item);
      continue;
    }
    geojsonFeatures.push({
      type: "Feature",
      geometry: bboxFeature.geometry,
      properties: {
        id: item.id,
        address: getFullAddress(item.location),
        incident: item.incident,
        short_description: item.short_description,
        district: item.district,
        date: item.date,
      },
    });
  }
  return { geojsonFeatures, confidentialAddresses, unmappable, flatList };
};

const main = async () => {
  mkdirSync(path.join(projectRoot, "dist"), { recursive: true });

  console.log(`Reading incidents from ${incidentsPath}`);
  const { geojsonFeatures, confidentialAddresses, unmappable, flatList } =
    await processIncidents(rawData);

  console.log(
    `\nmapped ${geojsonFeatures.length}, confidential ${confidentialAddresses.length}, ` +
      `unmappable ${unmappable.length}`
  );
  for (const item of unmappable) {
    console.log(`  unmappable: ${item.date} ${item.incident} @ "${item.location}"`);
  }

  // Save confidential addresses
  writeFileSync(
    path.join(projectRoot, "dist/confidential.json"),
    JSON.stringify(confidentialAddresses, null, 2)
  );

  // Incidents we couldn't place (freeways and streets OSM has no address data
  // for). They get their own panel so a murder never silently disappears just
  // because its street isn't in OpenStreetMap.
  writeFileSync(
    path.join(projectRoot, "dist/unmappable.json"),
    JSON.stringify(unmappable, null, 2)
  );

  // Every incident in the report, mapped or not, for the browsable list. The
  // status tells the list which rows can pan the map and which can't.
  const mappedIds = new Set(geojsonFeatures.map((f) => f.properties.id));
  const confidentialIds = new Set(confidentialAddresses.map((i) => i.id));
  const allIncidents = [...flatList]
    .sort((a, b) => {
      const byDate = new Date(a.date) - new Date(b.date);
      if (byDate) return byDate;
      return String(a.district).localeCompare(String(b.district));
    })
    .map((item) => ({
      ...item,
      status: mappedIds.has(item.id)
        ? "mapped"
        : confidentialIds.has(item.id)
          ? "confidential"
          : "unmappable",
    }));
  writeFileSync(
    path.join(projectRoot, "dist/incidents.json"),
    JSON.stringify(allIncidents, null, 2)
  );

  // Save valid GeoJSON FeatureCollection
  const geojson = {
    type: "FeatureCollection",
    features: geojsonFeatures,
  };
  writeFileSync(
    path.join(projectRoot, "dist/features.geojson"),
    JSON.stringify(geojson, null, 2)
  );

  const mapHtml = readFileSync(path.join(projectRoot, "src/map.html"), "utf-8");
  writeFileSync(path.join(projectRoot, "dist/index.html"), mapHtml);
  const aboutHtml = readFileSync(
    path.join(projectRoot, "src/about.html"),
    "utf-8"
  );
  writeFileSync(path.join(projectRoot, "dist/about.html"), aboutHtml);

  // Icons are authored in src/favicon.svg; the PNGs are rasterised from it and
  // committed so a build never needs a browser or an SVG renderer.
  for (const asset of STATIC_ASSETS) {
    copyFileSync(
      path.join(projectRoot, "src", asset),
      path.join(projectRoot, "dist", asset)
    );
  }
  console.log(`copied ${STATIC_ASSETS.length} static assets`);
};

if (import.meta.url === `file://${process.argv[1]}`) {
  main();
}

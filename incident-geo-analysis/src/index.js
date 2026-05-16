import {
  getFullAddress,
  getAddressBeginning,
  getAddressEnding,
  getLatLng,
  createBoundingBox,
} from "./geo.js";
import { writeFileSync, readFileSync, mkdirSync, existsSync } from "fs";
import ProgressBar from "progress";
import path from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const projectRoot = path.resolve(__dirname, "..");
const repoRoot = path.resolve(projectRoot, "..");

const AGENT_DATA_DIR = path.join(
  repoRoot,
  "agent-solution",
  "garland_tx_data_analysis"
);

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
      `Run the agent crew first (cd agent-solution/garland_tx_data_analysis && crewai run) ` +
      `or set INCIDENTS_JSON_PATH to point at a JSON file.`
  );
  process.exit(1);
}

const rawData = JSON.parse(readFileSync(incidentsPath, "utf-8"));

const normalizeIncidents = (data) => {
  // Accept either the flat list emitted by the agent crew or the legacy
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
    .map(({ date, incident, location, short_description, district }) => ({
      date,
      incident,
      location,
      district,
      short_description: short_description || incident,
    }));
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
  for (const item of flatList) {
    bar.tick();
    if (item.location === "ADDRESS CONFIDENTIAL") {
      confidentialAddresses.push(item);
      continue;
    }
    const fullAddress = getFullAddress(item.location);
    const beginningLatLng = await getLatLng(getAddressBeginning(fullAddress));
    const endingLatLng = await getLatLng(getAddressEnding(fullAddress));
    const bboxFeature = createBoundingBox(beginningLatLng, endingLatLng);
    if (bboxFeature) {
      geojsonFeatures.push({
        type: "Feature",
        geometry: bboxFeature.geometry,
        properties: {
          address: fullAddress,
          incident: item.incident,
          short_description: item.short_description,
          district: item.district,
          date: item.date,
        },
      });
    }
  }
  return { geojsonFeatures, confidentialAddresses };
};

const main = async () => {
  mkdirSync(path.join(projectRoot, "dist"), { recursive: true });

  console.log(`Reading incidents from ${incidentsPath}`);
  const { geojsonFeatures, confidentialAddresses } = await processIncidents(
    rawData
  );

  // Save confidential addresses
  writeFileSync(
    path.join(projectRoot, "dist/confidential.json"),
    JSON.stringify(confidentialAddresses, null, 2)
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
};

if (import.meta.url === `file://${process.argv[1]}`) {
  main();
}

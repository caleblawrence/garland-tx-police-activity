import {
  getFullAddress,
  getAddressBeginning,
  getAddressEnding,
  getLatLng,
  createBoundingBox,
} from "./geo.js";
import week41Data from "../../scrape-incidents/exported-incidents/districts_incidents_week_41.json" assert { type: "json" };
import { writeFileSync, readFileSync, mkdirSync } from "fs";
import ProgressBar from "progress";
import path from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const projectRoot = path.resolve(__dirname, "..");

const processIncidents = async (data) => {
  const flatIncidentList = Object.entries(data).map(([, feature]) => {
    return feature.flatMap((incident) => {
      return {
        date: incident.date,
        incident: incident.incident,
        location: incident.location,
      };
    });
  });
  const flatList = flatIncidentList.flat(Infinity);

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
    if (
      item &&
      typeof item === "object" &&
      "location" in item &&
      "incident" in item &&
      "date" in item
    ) {
      if (item.location === "ADDRESS CONFIDENTIAL") {
        confidentialAddresses.push(item);
        continue;
      }
      const partialAddress = item.location;
      const fullAddress = getFullAddress(partialAddress);
      console.log(`Mapping address: ${fullAddress}`);
      const beginningAddressRange = getAddressBeginning(fullAddress);
      const endingAddressRange = getAddressEnding(fullAddress);
      const beginningLatLng = await getLatLng(beginningAddressRange);
      const endingLatLng = await getLatLng(endingAddressRange);

      const bboxFeature = createBoundingBox(beginningLatLng, endingLatLng);

      if (bboxFeature) {
        geojsonFeatures.push({
          type: "Feature",
          geometry: bboxFeature.geometry,
          properties: {
            address: fullAddress,
            incident: item.incident,
            date: item.date,
          },
        });
      }
    }
  }
  return { geojsonFeatures, confidentialAddresses };
};

const main = async () => {
  mkdirSync(path.join(projectRoot, "dist"), { recursive: true });

  const { geojsonFeatures, confidentialAddresses } = await processIncidents(
    week41Data
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
};

if (import.meta.url === `file://${process.argv[1]}`) {
  main();
}

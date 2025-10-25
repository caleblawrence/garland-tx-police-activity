import fetch from "node-fetch";
import * as turf from "@turf/turf";
import week41Data from "../../scrape-incidents/exported-incidents/districts_incidents_week_41.json" assert { type: "json" };
import { writeFileSync, readFileSync, mkdirSync } from "fs";
import ProgressBar from "progress";
import path from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const projectRoot = path.resolve(__dirname, "..");

const init = async () => {
  mkdirSync(path.join(projectRoot, "dist"), { recursive: true });
  var flatIncidentList = Object.entries(week41Data).map(([, feature]) => {
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

  // Collect features for every address in flatList
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

      let bboxFeature = null;
      if (
        beginningLatLng &&
        endingLatLng &&
        beginningLatLng.lat === endingLatLng.lat &&
        beginningLatLng.lng === endingLatLng.lng
      ) {
        const buffer = 0.0005;
        const minLng = beginningLatLng.lng - buffer;
        const maxLng = beginningLatLng.lng + buffer;
        const minLat = beginningLatLng.lat - buffer;
        const maxLat = beginningLatLng.lat + buffer;
        bboxFeature = turf.bboxPolygon([minLng, minLat, maxLng, maxLat]);
      } else if (beginningLatLng && endingLatLng) {
        const point1 = turf.point([beginningLatLng.lng, beginningLatLng.lat]);
        const point2 = turf.point([endingLatLng.lng, endingLatLng.lat]);
        bboxFeature = turf.envelope(turf.featureCollection([point1, point2]));
      }
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

var getFullAddress = (partialAddress) => {
  return `${partialAddress} Garland TX`;
};

var getAddressBeginning = (address) => {
  // Extract the first part (e.g., '25XX')
  const firstPart = address.split(" ")[0];
  // Replace 'XX' or 'xx' with '00' to get the beginning of the range
  const match = firstPart.match(/^(\d+)[Xx]{2}$/);
  if (match) {
    var beginning = match[1].padStart(2, "0") + "00";
    return address.replace(firstPart, beginning);
  }
  // If not in '25XX' format, try to parse as a number
  const num = parseInt(firstPart, 10);
  if (!isNaN(num)) {
    return address;
  }
  return address;
};

var getAddressEnding = (address) => {
  // Extract the first part (e.g., '25XX')
  const firstPart = address.split(" ")[0];
  // Replace 'XX' or 'xx' with '99' to get the ending of the range
  const match = firstPart.match(/^(\d+)[Xx]{2}$/);
  if (match) {
    var ending = match[1].padStart(2, "0") + "99";
    return address.replace(firstPart, ending);
  }
  // If not in '25XX' format, try to parse as a number
  const num = parseInt(firstPart, 10);
  if (!isNaN(num)) {
    return address;
  }
  return address;
};

async function getLatLng(address) {
  // Use a geocoding API like OpenStreetMap Nominatim (no API key required)
  const query = encodeURIComponent(address + " Garland TX");
  const url = `https://nominatim.openstreetmap.org/search?q=${query}&format=json&limit=1`;

  try {
    const response = await fetch(url, {
      headers: { "User-Agent": "geo-analysis-app/1.0" },
    });
    const data = await response.json();
    if (Array.isArray(data) && data.length > 0) {
      return {
        lat: parseFloat(data[0].lat),
        lng: parseFloat(data[0].lon),
      };
    } else {
      return null;
    }
  } catch (error) {
    console.error("Geocoding error:", error);
    return null;
  }
}

init();

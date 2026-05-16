import fetch from "node-fetch";
import * as turf from "@turf/turf";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const CACHE_PATH = path.resolve(__dirname, "..", "dist", "geocode-cache.json");

// Nominatim's public usage policy is 1 request/second. Going faster gets us
// served HTML rate-limit pages, which used to silently crash JSON.parse and
// drop most of the dataset.
const MIN_INTERVAL_MS = 1100;
let lastRequestAt = 0;

const cache = existsSync(CACHE_PATH)
  ? JSON.parse(readFileSync(CACHE_PATH, "utf-8"))
  : {};

const saveCache = () => {
  if (Object.keys(cache).length === 0) return;
  try {
    mkdirSync(path.dirname(CACHE_PATH), { recursive: true });
    writeFileSync(CACHE_PATH, JSON.stringify(cache, null, 2));
  } catch (err) {
    console.error("Failed to write geocode cache:", err.message);
  }
};

process.on("exit", saveCache);
process.on("SIGINT", () => {
  saveCache();
  process.exit(130);
});

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export const getFullAddress = (partialAddress) => {
  return `${partialAddress} Garland TX`;
};

export const getAddressBeginning = (address) => {
  // Extract the first part (e.g., '25XX')
  const firstPart = address.split(" ")[0];
  // Replace 'XX' or 'xx' with '00' to get the beginning of the range
  const match = firstPart.match(/^(\d+)[Xx]{2}$/);
  if (match) {
    const blockNumber = match[1];
    // For single-digit blocks like '1XX', treat it as the 100 block.
    // For multi-digit blocks, pad with '00'.
    const beginning =
      blockNumber.length > 1 ? blockNumber + "00" : blockNumber.padEnd(3, "0");
    return address.replace(firstPart, beginning);
  }
  // If not in '25XX' format, try to parse as a number
  const num = parseInt(firstPart, 10);
  if (!isNaN(num)) {
    return address;
  }
  return address;
};

export const getAddressEnding = (address) => {
  // Extract the first part (e.g., '25XX')
  const firstPart = address.split(" ")[0];
  // Replace 'XX' or 'xx' with '99' to get the ending of the range
  const match = firstPart.match(/^(\d+)[Xx]{2}$/);
  if (match) {
    const blockNumber = match[1];
    // For single-digit blocks like '1XX', treat it as the 100 block (100-199).
    // For multi-digit blocks, pad with '99'.
    const ending =
      blockNumber.length > 1 ? blockNumber + "99" : blockNumber.padEnd(3, "9");
    return address.replace(firstPart, ending);
  }
  // If not in '25XX' format, try to parse as a number
  const num = parseInt(firstPart, 10);
  if (!isNaN(num)) {
    return address;
  }
  return address;
};

async function nominatimLookup(address, attempt = 1) {
  const wait = Math.max(0, lastRequestAt + MIN_INTERVAL_MS - Date.now());
  if (wait > 0) await sleep(wait);
  lastRequestAt = Date.now();

  const url = `https://nominatim.openstreetmap.org/search?q=${encodeURIComponent(address)}&format=json&limit=1`;
  const response = await fetch(url, {
    headers: { "User-Agent": "garland-tx-police-activity/1.0" },
  });

  if (response.status === 429 || response.status === 503) {
    if (attempt >= 3) {
      console.warn(`Nominatim rate-limited (${response.status}) after ${attempt} attempts: ${address}`);
      return null;
    }
    const backoff = 2000 * attempt;
    console.warn(`Nominatim ${response.status} for "${address}", retrying in ${backoff}ms`);
    await sleep(backoff);
    return nominatimLookup(address, attempt + 1);
  }

  const text = await response.text();
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    console.warn(`Non-JSON response for "${address}" (HTTP ${response.status}): ${text.slice(0, 80)}`);
    return null;
  }

  if (Array.isArray(data) && data.length > 0) {
    return { lat: parseFloat(data[0].lat), lng: parseFloat(data[0].lon) };
  }
  return null;
}

export async function getLatLng(address) {
  if (address in cache && cache[address] !== null) return cache[address];
  const result = await nominatimLookup(address);
  // Only cache successes. Caching nulls (rate-limit / not-found) would lock
  // us out of retrying those addresses on subsequent runs.
  if (result !== null) cache[address] = result;
  return result;
}

export function createBoundingBox(beginningLatLng, endingLatLng) {
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
    const distance = turf.distance(point1, point2, { units: "kilometers" });

    // If the distance is too large, just buffer the first point.
    if (distance > 2) {
      const buffer = 0.001; // A slightly larger buffer for blocks
      const minLng = beginningLatLng.lng - buffer;
      const maxLng = beginningLatLng.lng + buffer;
      const minLat = beginningLatLng.lat - buffer;
      const maxLat = beginningLatLng.lat + buffer;
      bboxFeature = turf.bboxPolygon([minLng, minLat, maxLng, maxLat]);
    } else {
      bboxFeature = turf.envelope(turf.featureCollection([point1, point2]));
    }
  }
  return bboxFeature;
}

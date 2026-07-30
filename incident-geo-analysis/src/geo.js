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

// left,top,right,bottom — a generous box around Garland used to bias results
// toward the right city without hard-filtering edge-of-city addresses.
const GARLAND_VIEWBOX = "-96.82,33.06,-96.48,32.78";

// Garland PD reports incidents at addresses in neighbouring cities (4200 Bunker
// Hill Rd is in Sachse), so results are validated against the wider area rather
// than the city limits. Dropping "Garland" from the query is what makes those
// resolve at all — but it also lets "1800 S FIRST ST" match Lufkin, 150 miles
// away, so every result is bounds-checked before it is trusted.
// Deliberately wider than the city limits: reports land on Bobtown Rd (32.848),
// Guthrie Rd (32.836) and the south end of Broadway Blvd (32.845), all of which
// a city-tight box rejected. Still far too small to admit the Lufkin and Fort
// Worth mismatches that the state-only query variant turns up.
const GARLAND_AREA = {
  minLat: 32.78,
  maxLat: 33.06,
  minLng: -96.82,
  maxLng: -96.48,
};

export const withinGarlandArea = (point) =>
  Boolean(point) &&
  point.lat >= GARLAND_AREA.minLat &&
  point.lat <= GARLAND_AREA.maxLat &&
  point.lng >= GARLAND_AREA.minLng &&
  point.lng <= GARLAND_AREA.maxLng;

// Tried in order. The bare-state variant recovers addresses that OSM files
// under an adjacent city; the bounds check above keeps it honest.
const CITY_VARIANTS = ["Garland TX", "TX"];

// Cached entries record whether Nominatim actually matched a house number.
// Legacy entries were bare {lat,lng} with no way to tell a real address from a
// whole-street fallback, so they are re-queried rather than trusted.
const CACHE_VERSION = 2;

const loadCache = () => {
  if (!existsSync(CACHE_PATH)) return {};
  const raw = JSON.parse(readFileSync(CACHE_PATH, "utf-8"));
  if (raw.__version === CACHE_VERSION) return raw;
  return { __version: CACHE_VERSION };
};

const cache = loadCache();

const saveCache = () => {
  cache.__version = CACHE_VERSION;
  if (Object.keys(cache).length <= 1) return;
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

// The PDF only ever gives us a block ("37XX W BUCKINGHAM RD"), never a house.
// A location without a block number (blank address column, "LBJ FRWY" with no
// number) can't be placed on a map, and geocoding it just resolves to the
// Garland city centroid — which used to draw boxes downtown for incidents that
// happened anywhere.
export const hasBlockNumber = (partialAddress) =>
  /^\s*\d+[Xx]{2}\b/.test(partialAddress || "");

// Middle of the block. "3750" is far more likely to exist in OSM than "3799",
// so it's a useful fallback anchor when an endpoint won't resolve.
export const getAddressMidpoint = (address) => {
  const firstPart = address.split(" ")[0];
  const match = firstPart.match(/^(\d+)[Xx]{2}$/);
  if (!match) return address;
  const blockNumber = match[1];
  const middle =
    blockNumber.length > 1 ? blockNumber + "50" : blockNumber.padEnd(2, "5") + "0";
  return address.replace(firstPart, middle);
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

  // addressdetails lets us tell a real house match from a whole-street fallback.
  // countrycodes + a Garland-centred viewbox stop "3799 SOMETHING RD" from
  // matching a same-named street in another state. viewbox biases ranking
  // without hard-filtering, so genuine edge-of-city addresses still resolve.
  const url =
    `https://nominatim.openstreetmap.org/search?q=${encodeURIComponent(address)}` +
    `&format=json&limit=1&addressdetails=1&countrycodes=us` +
    `&viewbox=${GARLAND_VIEWBOX}`;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  let response;
  try {
    response = await fetch(url, {
      headers: { "User-Agent": "garland-tx-police-activity/1.0" },
      signal: controller.signal,
    });
  } catch (err) {
    clearTimeout(timer);
    if (attempt >= 3) {
      console.warn(`Nominatim fetch failed after ${attempt} attempts: ${address} (${err.message})`);
      return null;
    }
    console.warn(`Nominatim fetch error for "${address}" (${err.message}), retrying`);
    await sleep(2000 * attempt);
    return nominatimLookup(address, attempt + 1);
  }
  clearTimeout(timer);

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
    const hit = data[0];
    return {
      lat: parseFloat(hit.lat),
      lng: parseFloat(hit.lon),
      // Nominatim happily answers "3799 W BUCKINGHAM RD" with the entire road
      // and returns an arbitrary point on it — 3.7km from the 3700 block in one
      // measured case. Those fallbacks carry no house_number, which is the one
      // reliable signal that we matched an actual address.
      precise: Boolean(hit.address && hit.address.house_number),
    };
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

// Measured span between the two ends of a real Garland block: 0.03–0.14 km.
// The old threshold was 2km, so a 1.7km smear between a real address and a
// random point on the same street passed as a legitimate block and got drawn
// as a giant box. Half a kilometre is generous for a city block.
export const MAX_BLOCK_SPAN_KM = 0.5;

// ~80m half-width. Used when we only have one trustworthy point, so the box
// says "somewhere in this block" instead of implying a precise address.
const POINT_PAD_DEG = 0.00075;

// A block running exactly north-south makes turf.envelope return a zero-width
// polygon, which renders as an invisible sliver. Keep every box clickable.
const MIN_HALF_SPAN_DEG = 0.00015;

const padBox = (lng, lat, pad = POINT_PAD_DEG) =>
  turf.bboxPolygon([lng - pad, lat - pad, lng + pad, lat + pad]);

/**
 * Build a block-sized box from the two ends of an address range.
 *
 * Only points that Nominatim matched to an actual house number are trusted;
 * whole-street fallbacks are the reason boxes used to land in random places.
 * Points without a `precise` flag are treated as trustworthy so existing
 * callers and tests that pass plain {lat,lng} keep working.
 */
export function createBoundingBox(beginningLatLng, endingLatLng) {
  const trusted = [beginningLatLng, endingLatLng].filter(
    (p) => p && p.precise !== false
  );
  if (trusted.length === 0) return null;

  if (trusted.length === 1) {
    return padBox(trusted[0].lng, trusted[0].lat);
  }

  const [a, b] = trusted;
  const point1 = turf.point([a.lng, a.lat]);
  const point2 = turf.point([b.lng, b.lat]);

  if (a.lat === b.lat && a.lng === b.lng) {
    return padBox(a.lng, a.lat);
  }

  // Both ends matched real addresses but they're implausibly far apart — most
  // likely two different segments of a long street. Fall back to the block
  // start rather than smearing a box across the gap.
  if (turf.distance(point1, point2, { units: "kilometers" }) > MAX_BLOCK_SPAN_KM) {
    return padBox(a.lng, a.lat);
  }

  const minLng = Math.min(a.lng, b.lng);
  const maxLng = Math.max(a.lng, b.lng);
  const minLat = Math.min(a.lat, b.lat);
  const maxLat = Math.max(a.lat, b.lat);
  const padLng = Math.max(0, MIN_HALF_SPAN_DEG - (maxLng - minLng) / 2);
  const padLat = Math.max(0, MIN_HALF_SPAN_DEG - (maxLat - minLat) / 2);

  return turf.bboxPolygon([
    minLng - padLng,
    minLat - padLat,
    maxLng + padLng,
    maxLat + padLat,
  ]);
}

/**
 * Resolve a raw PDF location ("37XX W BUCKINGHAM RD") to a block box.
 *
 * Queries the two ends of the block and, if either failed to match a real
 * address, tries the middle of the block as a third anchor. Returns null when
 * nothing trustworthy resolved — better to omit an incident than to draw it in
 * the wrong place.
 */
/**
 * Geocode one house number, accepting only a real address match inside the
 * Garland area. Tries the city-qualified query first, then the state-only
 * variant for addresses OSM files under a neighbouring city.
 */
async function geocodePrecisePoint(bareAddress) {
  for (const suffix of CITY_VARIANTS) {
    const point = await getLatLng(`${bareAddress} ${suffix}`);
    if (point && point.precise && withinGarlandArea(point)) return point;
  }
  return null;
}

export async function resolveBlockBox(partialLocation) {
  if (!hasBlockNumber(partialLocation)) return null;

  const begin = await geocodePrecisePoint(getAddressBeginning(partialLocation));
  const end = await geocodePrecisePoint(getAddressEnding(partialLocation));
  if (begin && end) return createBoundingBox(begin, end);

  // One end didn't resolve — the middle of the block is a likelier real address
  // than "…99" and gives us something to anchor the box on.
  const middle = await geocodePrecisePoint(getAddressMidpoint(partialLocation));
  const anchors = [begin, middle, end].filter(Boolean);
  if (anchors.length === 0) return null;

  return createBoundingBox(anchors[0], anchors[anchors.length - 1]);
}

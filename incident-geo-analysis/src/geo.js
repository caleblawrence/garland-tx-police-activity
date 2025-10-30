import fetch from "node-fetch";
import * as turf from "@turf/turf";

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

export async function getLatLng(address) {
  // Use a geocoding API like OpenStreetMap Nominatim (no API key required)
  const query = encodeURIComponent(address);
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

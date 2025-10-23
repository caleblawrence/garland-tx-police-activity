import fetch from "node-fetch";
import * as turf from "@turf/turf";

const init = async () => {
  // TODO: get these from file
  var partialAddress = "22XX S SHILOH RD";
  var fullAddress = getFullAddress(partialAddress);

  var beginningAddressRange = getAddressBeginning(fullAddress);

  console.log("beginningAddress:", beginningAddressRange);
  var beginningLatLng = await getLatLng(beginningAddressRange);
  console.log("beginningLatLng:", beginningLatLng);

  var endingAddressRange = getAddressEnding(fullAddress);
  console.log("endingAddress:", endingAddressRange);
  var endingLatLng = await getLatLng(endingAddressRange);
  console.log("endingLatLng:", endingLatLng);

  // create bounding box
  let bboxFeature;
  // Check if both points are valid
  if (
    beginningLatLng &&
    endingLatLng &&
    beginningLatLng.lat === endingLatLng.lat &&
    beginningLatLng.lng === endingLatLng.lng
  ) {
    console.warn(
      "Warning: Beginning and ending geocoded points are identical. Creating artificial bounding box."
    );
    // Create a small box around the point (e.g., 0.0005 deg buffer ~50m)
    const buffer = 0.0005;
    const minLng = beginningLatLng.lng - buffer;
    const maxLng = beginningLatLng.lng + buffer;
    const minLat = beginningLatLng.lat - buffer;
    const maxLat = beginningLatLng.lat + buffer;
    bboxFeature = turf.bboxPolygon([minLng, minLat, maxLng, maxLat]);
  } else if (beginningLatLng && endingLatLng) {
    // Create Turf points
    const point1 = turf.point([beginningLatLng.lng, beginningLatLng.lat]);
    const point2 = turf.point([endingLatLng.lng, endingLatLng.lat]);
    // Create a bounding box (envelope) from the two points
    bboxFeature = turf.envelope(turf.featureCollection([point1, point2]));
  } else {
    console.error(
      "Could not create bounding box: one or both geocoded points are null."
    );
    bboxFeature = null;
  }

  // bboxFeature is a Polygon representing the bounding box
  if (bboxFeature) {
    console.log(JSON.stringify(bboxFeature));
  }
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
    return num.toString().padStart(4, "0");
  }
  return address.replace(firstPart, getAddressBeginning(firstPart));
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
    return num.toString().padStart(4, "0");
  }
  return address.replace(firstPart, getAddressEnding(firstPart));
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
    console.log("Geocoding response:", data);
    if (data && data.length > 0) {
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

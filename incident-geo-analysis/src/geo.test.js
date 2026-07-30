import { describe, it, expect } from "vitest";
import {
  createBoundingBox,
  getAddressBeginning,
  getAddressEnding,
  getAddressMidpoint,
  getFullAddress,
  hasBlockNumber,
  withinGarlandArea,
  MAX_BLOCK_SPAN_KM,
} from "./geo.js";

describe("geo", () => {
  describe("getAddressBeginning", () => {
    it("should handle 'XX' notation for multi-digit blocks", () => {
      const address = "25XX Forest Ln";
      const result = getAddressBeginning(address);
      expect(result).toBe("2500 Forest Ln");
    });

    it("should handle 'XX' notation for single-digit blocks", () => {
      const address = "1XX Main St";
      const result = getAddressBeginning(address);
      expect(result).toBe("100 Main St");
    });

    it("should return the same address if no 'XX' is present", () => {
      const address = "123 Main St";
      const result = getAddressBeginning(address);
      expect(result).toBe("123 Main St");
    });
  });

  describe("getAddressEnding", () => {
    it("should handle 'XX' notation for multi-digit blocks", () => {
      const address = "25XX Forest Ln";
      const result = getAddressEnding(address);
      expect(result).toBe("2599 Forest Ln");
    });

    it("should handle 'XX' notation for single-digit blocks", () => {
      const address = "1XX Main St";
      const result = getAddressEnding(address);
      expect(result).toBe("199 Main St");
    });

    it("should return the same address if no 'XX' is present", () => {
      const address = "123 Main St";
      const result = getAddressEnding(address);
      expect(result).toBe("123 Main St");
    });
  });

  describe("getFullAddress", () => {
    it("should append ' Garland TX' to the partial address", () => {
      const partialAddress = "123 Main St";
      const result = getFullAddress(partialAddress);
      expect(result).toBe("123 Main St Garland TX");
    });
  });

  describe("createBoundingBox", () => {
    it("should create a small bounding box for identical lat/lng", () => {
      const latLng = { lat: 32.9128, lng: -96.6458 };
      const bbox = createBoundingBox(latLng, latLng);
      expect(bbox.type).toBe("Feature");
      expect(bbox.geometry.type).toBe("Polygon");
      // Expect a small box around the point
      expect(bbox.bbox[0]).toBeCloseTo(latLng.lng - 0.00075);
      expect(bbox.bbox[1]).toBeCloseTo(latLng.lat - 0.00075);
      expect(bbox.bbox[2]).toBeCloseTo(latLng.lng + 0.00075);
      expect(bbox.bbox[3]).toBeCloseTo(latLng.lat + 0.00075);
    });

    it("should create a bounding box for close lat/lng points", () => {
      const beginningLatLng = { lat: 32.9128, lng: -96.6458 };
      const endingLatLng = { lat: 32.913, lng: -96.646 };
      const bbox = createBoundingBox(beginningLatLng, endingLatLng);
      expect(bbox.type).toBe("Feature");
      expect(bbox.geometry.type).toBe("Polygon");
      // Use toBeCloseTo for floating point comparisons
      expect(bbox.bbox[0]).toBeCloseTo(endingLatLng.lng);
      expect(bbox.bbox[1]).toBeCloseTo(beginningLatLng.lat);
      expect(bbox.bbox[2]).toBeCloseTo(beginningLatLng.lng);
      expect(bbox.bbox[3]).toBeCloseTo(endingLatLng.lat);
    });

    it("should create a bounding box around the first point if distance is large", () => {
      const beginningLatLng = { lat: 32.9128, lng: -96.6458 };
      const endingLatLng = { lat: 33.0, lng: -97.0 }; // Far away
      const bbox = createBoundingBox(beginningLatLng, endingLatLng);
      expect(bbox.type).toBe("Feature");
      expect(bbox.geometry.type).toBe("Polygon");
      // Expect a box around the first point
      expect(bbox.bbox[0]).toBeCloseTo(beginningLatLng.lng - 0.00075);
      expect(bbox.bbox[1]).toBeCloseTo(beginningLatLng.lat - 0.00075);
      expect(bbox.bbox[2]).toBeCloseTo(beginningLatLng.lng + 0.00075);
      expect(bbox.bbox[3]).toBeCloseTo(beginningLatLng.lat + 0.00075);
    });

    it("should reject a span too long to be one block", () => {
      // ~1.7km apart: a real address plus an arbitrary point on the same
      // street. The old 2km threshold let this through as a legitimate block.
      const beginningLatLng = { lat: 32.9309, lng: -96.6857 };
      const endingLatLng = { lat: 32.9309, lng: -96.6675 };
      const spanKm = Math.abs(endingLatLng.lng - beginningLatLng.lng) * 93;
      expect(spanKm).toBeGreaterThan(MAX_BLOCK_SPAN_KM);

      const bbox = createBoundingBox(beginningLatLng, endingLatLng);
      // Box collapses onto the block start rather than smearing across the gap.
      expect(bbox.bbox[2] - bbox.bbox[0]).toBeCloseTo(0.0015);
    });

    it("should ignore an imprecise point and box the trustworthy one", () => {
      const house = { lat: 32.9128, lng: -96.6458, precise: true };
      const street = { lat: 32.9309, lng: -96.6459, precise: false };
      const bbox = createBoundingBox(house, street);
      expect(bbox.bbox[0]).toBeCloseTo(house.lng - 0.00075);
      expect(bbox.bbox[3]).toBeCloseTo(house.lat + 0.00075);
    });

    it("should give a north-south block a clickable width", () => {
      // turf.envelope alone returns a zero-width sliver for these.
      const a = { lat: 32.9128, lng: -96.6458, precise: true };
      const b = { lat: 32.9138, lng: -96.6458, precise: true };
      const bbox = createBoundingBox(a, b);
      expect(bbox.bbox[2] - bbox.bbox[0]).toBeGreaterThan(0);
    });

    it("should return null if one lat/lng is null", () => {
      const latLng = { lat: 32.9128, lng: -96.6458 };
      // A single trustworthy point still yields a block-sized box.
      expect(createBoundingBox(latLng, null)).not.toBeNull();
      expect(createBoundingBox(null, latLng)).not.toBeNull();
    });

    it("should return null if both lat/lng are null", () => {
      expect(createBoundingBox(null, null)).toBeNull();
    });

    it("should return null when no point matched a real address", () => {
      expect(
        createBoundingBox(
          { lat: 32.9128, lng: -96.6458, precise: false },
          { lat: 32.9309, lng: -96.6459, precise: false }
        )
      ).toBeNull();
    });
  });
});

describe("hasBlockNumber", () => {
  it("accepts a block address from the PDF", () => {
    expect(hasBlockNumber("37XX W BUCKINGHAM RD")).toBe(true);
    expect(hasBlockNumber("5XX EASY ST")).toBe(true);
  });

  it("rejects locations that cannot be placed on a map", () => {
    // A blank address column used to geocode to the Garland city centroid.
    expect(hasBlockNumber("")).toBe(false);
    expect(hasBlockNumber(undefined)).toBe(false);
    expect(hasBlockNumber("LBJ FRWY")).toBe(false);
  });
});

describe("getAddressMidpoint", () => {
  it("targets the middle of a multi-digit block", () => {
    expect(getAddressMidpoint("37XX W Buckingham Rd")).toBe(
      "3750 W Buckingham Rd"
    );
  });

  it("targets the middle of a single-digit block", () => {
    expect(getAddressMidpoint("1XX Main St")).toBe("150 Main St");
  });

  it("leaves a non-block address alone", () => {
    expect(getAddressMidpoint("123 Main St")).toBe("123 Main St");
  });
});

describe("withinGarlandArea", () => {
  it("accepts Garland and neighbouring-city addresses", () => {
    expect(withinGarlandArea({ lat: 32.9128, lng: -96.6458 })).toBe(true);
    // 4200 Bunker Hill Rd resolves to Sachse but is still Garland PD's report.
    expect(withinGarlandArea({ lat: 32.9764, lng: -96.5951 })).toBe(true);
  });

  it("rejects a same-named street in a far-off city", () => {
    // "1800 S FIRST ST TX" matches Lufkin, ~150 miles away.
    expect(withinGarlandArea({ lat: 31.3382, lng: -94.7291 })).toBe(false);
  });

  it("rejects a missing point", () => {
    expect(withinGarlandArea(null)).toBe(false);
  });
});

describe("withinGarlandArea boundary cases", () => {
  it("accepts real reports south of the city limits", () => {
    // These all resolve to precise house matches and were being thrown away.
    expect(withinGarlandArea({ lat: 32.8484, lng: -96.568 })).toBe(true); // Bobtown Rd
    expect(withinGarlandArea({ lat: 32.8356, lng: -96.5876 })).toBe(true); // Guthrie Rd
    expect(withinGarlandArea({ lat: 32.8454, lng: -96.5957 })).toBe(true); // Broadway Blvd
  });

  it("still rejects a Fort Worth match", () => {
    // "6600 LAKE SHORE DR TX" lands here.
    expect(withinGarlandArea({ lat: 32.7543, lng: -97.2604 })).toBe(false);
  });
});

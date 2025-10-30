import { describe, it, expect } from "vitest";
import {
  createBoundingBox,
  getAddressBeginning,
  getAddressEnding,
  getFullAddress,
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
      expect(bbox.bbox[0]).toBeCloseTo(latLng.lng - 0.0005);
      expect(bbox.bbox[1]).toBeCloseTo(latLng.lat - 0.0005);
      expect(bbox.bbox[2]).toBeCloseTo(latLng.lng + 0.0005);
      expect(bbox.bbox[3]).toBeCloseTo(latLng.lat + 0.0005);
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
      expect(bbox.bbox[0]).toBeCloseTo(beginningLatLng.lng - 0.001);
      expect(bbox.bbox[1]).toBeCloseTo(beginningLatLng.lat - 0.001);
      expect(bbox.bbox[2]).toBeCloseTo(beginningLatLng.lng + 0.001);
      expect(bbox.bbox[3]).toBeCloseTo(beginningLatLng.lat + 0.001);
    });

    it("should return null if one lat/lng is null", () => {
      const latLng = { lat: 32.9128, lng: -96.6458 };
      expect(createBoundingBox(latLng, null)).toBeNull();
      expect(createBoundingBox(null, latLng)).toBeNull();
    });

    it("should return null if both lat/lng are null", () => {
      expect(createBoundingBox(null, null)).toBeNull();
    });
  });
});

import { describe, it, expect } from "vitest";
import {
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
});

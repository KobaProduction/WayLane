import { describe, expect, test } from "bun:test";
import {
  coreKits,
  createCoreConfigTemplate,
  getCoreKit,
  supportedCoreKinds,
  validateCoreConfig,
  wireguard,
  xray
} from "@waylane/core-kit";

describe("core registry", () => {
  test("exposes stable supported core kinds", () => {
    expect(supportedCoreKinds).toEqual(["xray", "wg"]);
    expect(coreKits.xray.kind).toBe("xray");
    expect(coreKits.wg.kind).toBe("wg");
    expect(getCoreKit("xray").label).toBe("Xray");
    expect(getCoreKit("wg").label).toBe("WireGuard");
  });

  test("creates and validates default Xray JSON through the facade", () => {
    const template = createCoreConfigTemplate("xray");
    expect(template.kind).toBe("xray");

    const parsed = JSON.parse(template.configJson) as { inbounds?: unknown[]; outbounds?: unknown[] };
    expect(Array.isArray(parsed.inbounds)).toBe(true);
    expect(Array.isArray(parsed.outbounds)).toBe(true);

    const result = validateCoreConfig("xray", template.configJson);
    expect(result.ok).toBe(true);
  });

  test("creates and validates default WireGuard JSON through the facade", () => {
    const template = createCoreConfigTemplate("wg");
    expect(template.kind).toBe("wg");
    expect(template.generated?.wireGuardKeyPair).toBeTruthy();

    const parsed = JSON.parse(template.configJson) as { interface_name?: string; private_key?: string };
    expect(parsed.interface_name).toBe("wg0");
    expect(typeof parsed.private_key).toBe("string");

    const result = validateCoreConfig("wg", template.configJson);
    expect(result.ok).toBe(true);
  });

  test("normalizes JSON parse failures", () => {
    const result = validateCoreConfig("wg", "{not json");
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.issues[0]?.code).toBe("CORE_KIT_INVALID_JSON");
    }
  });

  test("delegates Xray validation failures", () => {
    const result = validateCoreConfig("xray", []);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.issues[0]?.code).toBe("XCK_XRAY_STRICT_EXPECTED_OBJECT");
    }
  });

  test("delegates WireGuard validation failures", () => {
    const result = validateCoreConfig("wg", {
      interface_name: "wg0",
      private_key: "bad",
      listen_port: 51820,
      address: ["10.0.0.1/8"]
    });
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.issues.length).toBeGreaterThan(0);
      expect(result.issues[0]?.code).toMatch(/^WG_/);
    }
  });

  test("re-exports underlying browser-safe namespaces", () => {
    expect(typeof xray.createDefaultXrayCoreConfigJson).toBe("function");
    expect(typeof wireguard.generateWireGuardKeyPair).toBe("function");
  });
});

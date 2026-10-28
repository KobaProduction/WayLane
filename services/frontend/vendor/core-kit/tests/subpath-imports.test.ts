import { describe, expect, test } from "bun:test";

describe("package subpaths", () => {
  test("loads root and subpath exports", async () => {
    const root = await import("@waylane/core-kit");
    const xray = await import("@waylane/core-kit/xray");
    const xrayGenerators = await import("@waylane/core-kit/xray/generators");
    const wireguard = await import("@waylane/core-kit/wireguard");

    expect(typeof root.createCoreConfigTemplate).toBe("function");
    expect(typeof xray.validateStrictXrayConfig).toBe("function");
    expect(typeof xrayGenerators.createDefaultXrayCoreConfigJson).toBe("function");
    expect(typeof wireguard.generateWireGuardKeyPair).toBe("function");
  });
});

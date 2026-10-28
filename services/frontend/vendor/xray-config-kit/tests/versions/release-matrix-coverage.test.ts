import { describe, expect, it } from "bun:test";
import { getXrayParityReleases, validateStrictXrayConfig } from "../../src/index.js";

describe("xray selected release parity matrix", () => {
  it("validates loader protocols for every selected release", () => {
    for (const release of getXrayParityReleases()) {
      for (const entry of release.inboundProtocols) {
        const result = validateStrictXrayConfig({
          inbounds: [
            {
              protocol: entry.protocol,
              tag: `in-${entry.protocol}`,
              ...(entry.protocol === "tun" ? {} : { port: 10000 }),
              settings: {}
            }
          ]
        }, { releaseTag: release.tag });

        expect(result.ok, `${release.tag} inbound ${entry.protocol}: ${result.issues.map((issue) => issue.message).join("; ")}`).toBe(true);
      }

      for (const entry of release.outboundProtocols) {
        const result = validateStrictXrayConfig({
          outbounds: [
            {
              protocol: entry.protocol,
              tag: `out-${entry.protocol}`,
              settings: {}
            }
          ]
        }, { releaseTag: release.tag });

        expect(result.ok, `${release.tag} outbound ${entry.protocol}: ${result.issues.map((issue) => issue.message).join("; ")}`).toBe(true);
      }
    }
  });

  it("validates transport aliases and security names for every selected release", () => {
    for (const release of getXrayParityReleases()) {
      for (const network of Object.keys(release.transportAliases)) {
        const result = validateStrictXrayConfig({
          inbounds: [
            {
              protocol: "vless",
              tag: `vless-${network}`,
              port: 443,
              settings: { clients: [], decryption: "none" },
              streamSettings: { network, security: "none" }
            }
          ]
        }, { releaseTag: release.tag });

        expect(result.ok, `${release.tag} network ${network}: ${result.issues.map((issue) => issue.message).join("; ")}`).toBe(true);
      }

      for (const security of release.securityTypes.filter((item) => item !== "xtls")) {
        const result = validateStrictXrayConfig({
          inbounds: [
            {
              protocol: "vless",
              tag: `vless-${security}`,
              port: 443,
              settings: { clients: [], decryption: "none" },
              streamSettings: { network: "tcp", security }
            }
          ]
        }, { releaseTag: release.tag });

        expect(result.ok, `${release.tag} security ${security}: ${result.issues.map((issue) => issue.message).join("; ")}`).toBe(true);
      }
    }
  });
});

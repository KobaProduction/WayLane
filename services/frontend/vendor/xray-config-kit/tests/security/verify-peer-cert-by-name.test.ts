import { describe, expect, it } from "bun:test";
import {
  buildXrayConfig,
  createProfile,
  importXrayConfig,
  normalizeProfile,
  validateProfile
} from "../../src/index.js";
import { latestGeneratedRelease } from "../helpers/xray-releases.js";
import type { Profile } from "../../src/index.js";

function tlsInboundProfile(verifyPeerCertByName: string | string[]): Profile {
  return createProfile({
    name: "tls-verify-peer",
    inbounds: [
      {
        kind: "inbound",
        protocol: "vmess",
        tag: "vmess-tls",
        listen: "0.0.0.0",
        port: 443,
        clients: [
          {
            protocol: "vmess",
            id: "22222222-2222-4222-8222-222222222222",
            security: "auto",
            email: "bob"
          }
        ],
        security: {
          type: "tls",
          serverName: "edge.example.com",
          verifyPeerCertByName
        },
        transport: {
          type: "tcp",
          header: { type: "none" }
        }
      }
    ]
  });
}

describe("verifyPeerCertByName Xray string / legacy array", () => {
  it("compiles a string value without crashing and keeps Xray's string form", () => {
    const built = buildXrayConfig(tlsInboundProfile("a.example.com,b.example.com"), {
      xrayVersion: latestGeneratedRelease.version
    });

    expect(built.issues.filter((issue) => issue.severity === "error")).toEqual([]);
    expect(built.config.inbounds?.[0]?.streamSettings).toMatchObject({
      security: "tls",
      tlsSettings: {
        verifyPeerCertByName: "a.example.com,b.example.com"
      }
    });
  });

  it("joins a legacy array into the Xray string form", () => {
    const built = buildXrayConfig(tlsInboundProfile(["a.example.com", "b.example.com"]), {
      xrayVersion: latestGeneratedRelease.version
    });

    expect(built.issues.filter((issue) => issue.severity === "error")).toEqual([]);
    expect(
      (built.config.inbounds?.[0]?.streamSettings as { tlsSettings?: { verifyPeerCertByName?: unknown } })?.tlsSettings
        ?.verifyPeerCertByName
    ).toBe("a.example.com,b.example.com");
  });

  it("accepts a string on the profile schema", () => {
    const result = validateProfile(tlsInboundProfile("example.com"));
    expect(result.ok).toBe(true);
    expect(result.profile?.inbounds[0] && "security" in result.profile.inbounds[0] ? result.profile.inbounds[0].security : undefined).toMatchObject({
      type: "tls",
      verifyPeerCertByName: ["example.com"]
    });
  });

  it("imports both the current string and the old array from Xray JSON", () => {
    const asString = importXrayConfig({
      inbounds: [
        {
          protocol: "vmess",
          tag: "vmess-tls",
          port: 443,
          settings: { clients: [] },
          streamSettings: {
            network: "tcp",
            security: "tls",
            tlsSettings: { verifyPeerCertByName: "a.example.com,b.example.com" }
          }
        }
      ]
    });
    const asArray = importXrayConfig({
      inbounds: [
        {
          protocol: "vmess",
          tag: "vmess-tls",
          port: 443,
          settings: { clients: [] },
          streamSettings: {
            network: "tcp",
            security: "tls",
            tlsSettings: { verifyPeerCertByName: ["a.example.com", "b.example.com"] }
          }
        }
      ]
    });

    expect(asString.profile.inbounds[0] && "security" in asString.profile.inbounds[0] ? asString.profile.inbounds[0].security : undefined).toMatchObject({
      verifyPeerCertByName: ["a.example.com", "b.example.com"]
    });
    expect(asArray.profile.inbounds[0] && "security" in asArray.profile.inbounds[0] ? asArray.profile.inbounds[0].security : undefined).toMatchObject({
      verifyPeerCertByName: ["a.example.com", "b.example.com"]
    });
  });

  it("stringifies outbound streamSettings arrays so Xray JSON stays a string", () => {
    const profile = normalizeProfile({
      schemaVersion: "xck.v1",
      inbounds: [],
      outbounds: [
        {
          protocol: "vless",
          tag: "proxy",
          settings: {},
          streamSettings: {
            network: "tcp",
            security: "tls",
            tlsSettings: {
              serverName: "edge.example.com",
              verifyPeerCertByName: ["a.example.com", "b.example.com"]
            }
          }
        }
      ]
    });

    const built = buildXrayConfig(profile, { xrayVersion: latestGeneratedRelease.version });
    expect(
      (built.config.outbounds?.[0]?.streamSettings as { tlsSettings?: { verifyPeerCertByName?: unknown } })?.tlsSettings
        ?.verifyPeerCertByName
    ).toBe("a.example.com,b.example.com");
  });
});

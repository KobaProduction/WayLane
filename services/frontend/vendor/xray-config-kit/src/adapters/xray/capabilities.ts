import type { XrayCapabilities } from "./types.js";

export type CapabilityFlagMap = Readonly<Record<string, boolean>>;

export type CapabilitySummary = {
  readonly adapterId: string;
  readonly xrayVersionRange: string;
  readonly latestTestedVersion: string;
  readonly protocols: CapabilityFlagMap;
  readonly transports: CapabilityFlagMap;
  readonly security: CapabilityFlagMap;
  readonly fingerprints: CapabilityFlagMap;
  readonly alpn: CapabilityFlagMap;
};

function toFlagMap(values: readonly string[]): CapabilityFlagMap {
  return Object.fromEntries(values.map((value) => [value, true]));
}

export function buildCapabilitySummary(capabilities: XrayCapabilities): CapabilitySummary {
  return {
    adapterId: capabilities.adapterId,
    xrayVersionRange: capabilities.xrayVersionRange,
    latestTestedVersion: capabilities.latestTestedVersion,
    protocols: toFlagMap(capabilities.protocols),
    transports: toFlagMap(capabilities.transports),
    security: toFlagMap(capabilities.securities),
    fingerprints: toFlagMap(capabilities.fingerprints),
    alpn: toFlagMap(capabilities.alpn)
  };
}

export function capabilitySummary(capabilities: XrayCapabilities): CapabilitySummary {
  return buildCapabilitySummary(capabilities);
}

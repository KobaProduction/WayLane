export {
  buildCapabilitySummary,
  capabilitySummary,
  compatibilityMatrix,
  getCapabilities,
  getCapabilitySummary,
  getGeneratedOutboundFormMetadata,
  getGeneratedRoutingRuleFields,
  getXrayAdapter,
  registeredXrayAdapters
} from "./xray/index.js";
export type { CompatibilityMatrix, FeatureSupport, XrayAdapter, XrayCapabilities, XrayFeature } from "./xray/index.js";
export type { CapabilityFlagMap, CapabilitySummary } from "./xray/index.js";
export type { XrayGeneratedFormField, XrayOutboundFormMetadata } from "./xray/index.js";

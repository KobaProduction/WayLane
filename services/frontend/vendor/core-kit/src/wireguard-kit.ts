import {
  createDefaultWireGuardCoreDraft,
  generateWireGuardCoreConfigJsonFromDraft,
  validateWireGuardCoreConfig
} from "@waylane/wireguard-config-kit";
import { parseConfigInput } from "./json.js";
import type {
  CoreConfigTemplateResult,
  CoreKit,
  CoreKitValidationIssue,
  CoreKitValidationResult
} from "./types.js";
import type { WireGuardCoreConfig } from "@waylane/wireguard-config-kit";

function mapWireGuardIssue(issue: {
  readonly code: string;
  readonly path: string;
  readonly message: string;
}): CoreKitValidationIssue {
  return {
    code: issue.code,
    path: issue.path,
    message: issue.message,
    severity: "error"
  };
}

function createDefaultConfigJson(): CoreConfigTemplateResult {
  const draft = createDefaultWireGuardCoreDraft();
  return {
    kind: "wg",
    configJson: generateWireGuardCoreConfigJsonFromDraft(draft),
    generated: {
      wireGuardKeyPair: {
        privateKey: draft.privateKey,
        publicKey: draft.publicKey
      }
    }
  };
}

function validateConfig(input: unknown): CoreKitValidationResult<WireGuardCoreConfig> {
  const parsed = parseConfigInput(input);
  if (!parsed.ok) return parsed;

  const result = validateWireGuardCoreConfig(parsed.config);
  if (result.ok) {
    return {
      ok: true,
      config: result.config,
      issues: []
    };
  }

  return {
    ok: false,
    issues: result.issues.map(mapWireGuardIssue)
  };
}

export const wireGuardKit: CoreKit<WireGuardCoreConfig> = {
  kind: "wg",
  label: "WireGuard",
  browserSafe: true,
  capabilities: {
    coreConfigTemplate: true,
    rawConfigValidation: true,
    keyGeneration: true,
    formDrafts: true,
    clientLinks: false
  },
  createDefaultConfigJson,
  validateConfig
};

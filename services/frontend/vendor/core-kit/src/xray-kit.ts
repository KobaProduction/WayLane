import {
  createDefaultXrayCoreConfigJson,
  validateStrictXrayConfig
} from "@waylane/xray-config-kit";
import { parseConfigInput } from "./json.js";
import type {
  CoreConfigTemplateResult,
  CoreKit,
  CoreKitValidationIssue,
  CoreKitValidationOptions,
  CoreKitValidationResult
} from "./types.js";
import type { XrayConfig } from "@waylane/xray-config-kit";

function mapXrayIssue(issue: {
  readonly code: string;
  readonly path: string;
  readonly message: string;
  readonly severity?: "error" | "warning" | "info";
}): CoreKitValidationIssue {
  return {
    code: issue.code,
    path: issue.path,
    message: issue.message,
    severity: issue.severity
  };
}

function createDefaultConfigJson(): CoreConfigTemplateResult {
  return {
    kind: "xray",
    configJson: createDefaultXrayCoreConfigJson()
  };
}

function validateConfig(
  input: unknown,
  options: CoreKitValidationOptions = {}
): CoreKitValidationResult<XrayConfig> {
  const parsed = parseConfigInput(input);
  if (!parsed.ok) return parsed;

  const result = validateStrictXrayConfig(parsed.config, {
    xrayVersion: options.xrayVersion
  });

  if (result.ok && result.config && result.issues.length === 0) {
    return {
      ok: true,
      config: result.config,
      issues: []
    };
  }

  return {
    ok: false,
    issues: result.issues.map(mapXrayIssue)
  };
}

export const xrayKit: CoreKit<XrayConfig> = {
  kind: "xray",
  label: "Xray",
  browserSafe: true,
  capabilities: {
    coreConfigTemplate: true,
    rawConfigValidation: true,
    keyGeneration: true,
    formDrafts: true,
    clientLinks: true
  },
  createDefaultConfigJson,
  validateConfig
};

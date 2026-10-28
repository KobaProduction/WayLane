import type { CoreKitValidationResult } from "./types.js";

export function parseConfigInput(input: unknown): CoreKitValidationResult<unknown> {
  if (typeof input !== "string") {
    return { ok: true, config: input, issues: [] };
  }

  try {
    return { ok: true, config: JSON.parse(input), issues: [] };
  } catch (error) {
    return {
      ok: false,
      issues: [
        {
          code: "CORE_KIT_INVALID_JSON",
          path: "/",
          message: error instanceof Error ? error.message : "Configuration must be valid JSON.",
          severity: "error"
        }
      ]
    };
  }
}

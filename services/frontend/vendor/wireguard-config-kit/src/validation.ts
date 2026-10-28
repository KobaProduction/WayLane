import { z } from "zod";
import { normalizeWireGuardKey } from "./base64.js";
import { normalizeIpInterface } from "./ip.js";
import type {
  JsonValue,
  WireGuardCoreConfig,
  WireGuardValidationIssue,
  WireGuardValidationResult
} from "./types.js";

const jsonValueSchema: z.ZodType<JsonValue> = z.lazy(() =>
  z.union([
    z.string(),
    z.number().finite(),
    z.boolean(),
    z.null(),
    z.array(jsonValueSchema),
    z.record(jsonValueSchema)
  ])
);

const rawWireGuardCoreConfigSchema = z.object({
  interface_name: z.string(),
  private_key: z.string(),
  pre_shared_key: z.string().optional(),
  listen_port: z.number(),
  address: z.array(z.string())
}).catchall(jsonValueSchema);

function issue(path: string, code: string, message: string): WireGuardValidationIssue {
  return { path, code, message };
}

function pathForZod(path: readonly (string | number)[]): string {
  if (path.length === 0) return "/";
  return `/${path.map(String).join("/")}`;
}

function validateInterfaceName(value: string): string {
  const interfaceName = value.trim();
  if (!interfaceName) throw new Error("interface_name is required.");
  if (interfaceName.includes(",")) throw new Error("character ',' is not allowed in interface_name.");
  if (interfaceName.includes("<=>")) throw new Error("character '<=>' is not allowed in interface_name.");
  return interfaceName;
}

function validateListenPort(value: number): number {
  if (!Number.isInteger(value) || value <= 0 || value > 65535) {
    throw new Error("listen_port must be an integer between 1 and 65535.");
  }
  return value;
}

function normalizeConfig(input: z.infer<typeof rawWireGuardCoreConfigSchema>): WireGuardCoreConfig {
  const output = {
    ...input,
    interface_name: validateInterfaceName(input.interface_name),
    private_key: normalizeWireGuardKey(input.private_key, "private_key"),
    listen_port: validateListenPort(input.listen_port),
    address: input.address.map(normalizeIpInterface)
  };

  if (typeof input.pre_shared_key === "string" && input.pre_shared_key.trim()) {
    output.pre_shared_key = normalizeWireGuardKey(input.pre_shared_key, "pre_shared_key");
  } else {
    delete output.pre_shared_key;
  }

  return output as WireGuardCoreConfig;
}

export function validateWireGuardCoreConfig(input: unknown): WireGuardValidationResult {
  const parsed = rawWireGuardCoreConfigSchema.safeParse(input);
  if (!parsed.success) {
    return {
      ok: false,
      issues: parsed.error.issues.map((zodIssue) =>
        issue(
          pathForZod(zodIssue.path.filter((part): part is string | number => typeof part === "string" || typeof part === "number")),
          "WG_SCHEMA_INVALID_CORE_CONFIG",
          zodIssue.message
        )
      )
    };
  }

  const issues: WireGuardValidationIssue[] = [];
  let config: WireGuardCoreConfig | undefined;

  try {
    config = normalizeConfig(parsed.data);
  } catch (error) {
    issues.push(issue("/", "WG_SEMANTIC_INVALID_CORE_CONFIG", error instanceof Error ? error.message : "Invalid WireGuard core config."));
  }

  if (!config) return { ok: false, issues };
  return { ok: true, config, issues: [] };
}

export function assertValidWireGuardCoreConfig(input: unknown): WireGuardCoreConfig {
  const result = validateWireGuardCoreConfig(input);
  if (!result.ok) {
    const firstIssue = result.issues[0];
    throw new Error(firstIssue ? `${firstIssue.path}: ${firstIssue.message}` : "Invalid WireGuard core config.");
  }
  return result.config;
}

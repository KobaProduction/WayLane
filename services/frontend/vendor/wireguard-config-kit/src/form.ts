import { normalizeWireGuardKey } from "./base64.js";
import {
  createWireGuardCoreConfig,
  generateWireGuardKeyPair,
  getWireGuardPublicKey
} from "./core.js";
import { normalizeIpInterface } from "./ip.js";
import type {
  CreateWireGuardCoreConfigOptions,
  JsonValue,
  WireGuardCoreConfig,
  WireGuardValidationIssue
} from "./types.js";

export type WireGuardCoreDraft = {
  readonly interfaceName: string;
  readonly privateKey: string;
  readonly publicKey: string;
  readonly preSharedKey: string;
  readonly listenPort: number | string;
  readonly address: readonly string[];
  readonly extra: Record<string, JsonValue>;
};

export type CreateDefaultWireGuardCoreDraftOptions = Partial<Omit<WireGuardCoreDraft, "publicKey" | "extra">> & {
  readonly publicKey?: string;
  readonly extra?: Record<string, JsonValue>;
};

export type WireGuardCoreFieldKey =
  | "interfaceName"
  | "privateKey"
  | "publicKey"
  | "preSharedKey"
  | "listenPort"
  | "address"
  | "extra";

export type WireGuardCoreFormFieldInput =
  | "text"
  | "secret"
  | "readonly"
  | "number"
  | "string-array"
  | "json-object";

export type WireGuardCoreFormField = {
  readonly key: WireGuardCoreFieldKey;
  readonly configKey?: string;
  readonly label: string;
  readonly input: WireGuardCoreFormFieldInput;
  readonly required: boolean;
  readonly readOnly?: boolean;
  readonly placeholder?: string;
};

export type WireGuardCoreFieldVisibility = Record<WireGuardCoreFieldKey, boolean>;

export type WireGuardCoreFormCapabilities = {
  readonly fieldOrder: readonly WireGuardCoreFieldKey[];
  readonly fields: Record<WireGuardCoreFieldKey, WireGuardCoreFormField>;
  readonly defaults: Pick<WireGuardCoreDraft, "interfaceName" | "listenPort" | "address">;
};

const defaultAddress = ["10.0.0.1/8"] as const;
const fieldOrder = [
  "interfaceName",
  "privateKey",
  "publicKey",
  "preSharedKey",
  "listenPort",
  "address",
  "extra"
] as const satisfies readonly WireGuardCoreFieldKey[];

const fields: Record<WireGuardCoreFieldKey, WireGuardCoreFormField> = {
  interfaceName: {
    key: "interfaceName",
    configKey: "interface_name",
    label: "Interface name",
    input: "text",
    required: true,
    placeholder: "wg0"
  },
  privateKey: {
    key: "privateKey",
    configKey: "private_key",
    label: "Private key",
    input: "secret",
    required: true
  },
  publicKey: {
    key: "publicKey",
    label: "Public key",
    input: "readonly",
    required: false,
    readOnly: true
  },
  preSharedKey: {
    key: "preSharedKey",
    configKey: "pre_shared_key",
    label: "Pre-shared key",
    input: "secret",
    required: false
  },
  listenPort: {
    key: "listenPort",
    configKey: "listen_port",
    label: "Listen port",
    input: "number",
    required: true,
    placeholder: "51820"
  },
  address: {
    key: "address",
    configKey: "address",
    label: "Address",
    input: "string-array",
    required: false,
    placeholder: "10.0.0.1/8"
  },
  extra: {
    key: "extra",
    label: "Extra fields",
    input: "json-object",
    required: false
  }
};

function issue(path: string, code: string, message: string): WireGuardValidationIssue {
  return { path, code, message };
}

function publicKeyOrEmpty(privateKey: string): string {
  try {
    return getWireGuardPublicKey(privateKey);
  } catch {
    return "";
  }
}

function parseListenPort(value: number | string): number | undefined {
  if (typeof value === "number") return Number.isInteger(value) ? value : undefined;
  const trimmed = value.trim();
  if (!/^\d+$/.test(trimmed)) return undefined;
  return Number(trimmed);
}

function assertPlainExtra(value: Record<string, JsonValue>): void {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("extra must be a JSON object.");
  }
}

function draftToOptions(draft: WireGuardCoreDraft): CreateWireGuardCoreConfigOptions {
  const listenPort = parseListenPort(draft.listenPort);
  if (listenPort === undefined) {
    throw new Error("listenPort must be an integer between 1 and 65535.");
  }

  return {
    interfaceName: draft.interfaceName,
    privateKey: draft.privateKey,
    preSharedKey: draft.preSharedKey.trim() ? draft.preSharedKey : undefined,
    listenPort,
    address: [...draft.address],
    extra: { ...draft.extra }
  };
}

export function createDefaultWireGuardCoreDraft(
  options: CreateDefaultWireGuardCoreDraftOptions = {}
): WireGuardCoreDraft {
  const generated = options.privateKey === undefined ? generateWireGuardKeyPair() : undefined;
  const privateKey = options.privateKey ?? generated?.privateKey ?? "";

  return {
    interfaceName: options.interfaceName ?? "wg0",
    privateKey,
    publicKey: options.publicKey ?? (generated ? generated.publicKey : publicKeyOrEmpty(privateKey)),
    preSharedKey: options.preSharedKey ?? "",
    listenPort: options.listenPort ?? 51820,
    address: [...(options.address ?? defaultAddress)],
    extra: { ...(options.extra ?? {}) }
  };
}

export function syncWireGuardCoreDraftPublicKey(draft: WireGuardCoreDraft): WireGuardCoreDraft {
  return {
    ...draft,
    publicKey: publicKeyOrEmpty(draft.privateKey)
  };
}

export function getWireGuardCoreFormCapabilities(): WireGuardCoreFormCapabilities {
  return {
    fieldOrder,
    fields: Object.fromEntries(
      Object.entries(fields).map(([key, field]) => [key, { ...field }])
    ) as Record<WireGuardCoreFieldKey, WireGuardCoreFormField>,
    defaults: {
      interfaceName: "wg0",
      listenPort: 51820,
      address: [...defaultAddress]
    }
  };
}

export function getWireGuardCoreFieldVisibility(
  _draft?: WireGuardCoreDraft,
  _capabilities: WireGuardCoreFormCapabilities = getWireGuardCoreFormCapabilities()
): WireGuardCoreFieldVisibility {
  return {
    interfaceName: true,
    privateKey: true,
    publicKey: true,
    preSharedKey: true,
    listenPort: true,
    address: true,
    extra: true
  };
}

export function validateWireGuardCoreDraft(draft: WireGuardCoreDraft): WireGuardValidationIssue[] {
  const issues: WireGuardValidationIssue[] = [];

  const interfaceName = draft.interfaceName.trim();
  if (!interfaceName) {
    issues.push(issue("/interfaceName", "WG_FORM_INTERFACE_NAME_REQUIRED", "Interface name is required."));
  } else {
    if (interfaceName.includes(",")) {
      issues.push(issue("/interfaceName", "WG_FORM_INTERFACE_NAME_COMMA", "Interface name cannot contain ','."));
    }
    if (interfaceName.includes("<=>")) {
      issues.push(issue("/interfaceName", "WG_FORM_INTERFACE_NAME_SEPARATOR", "Interface name cannot contain '<=>'."));
    }
  }

  try {
    normalizeWireGuardKey(draft.privateKey, "private_key");
  } catch (error) {
    issues.push(issue("/privateKey", "WG_FORM_PRIVATE_KEY_INVALID", error instanceof Error ? error.message : "Invalid private_key."));
  }

  if (draft.preSharedKey.trim()) {
    try {
      normalizeWireGuardKey(draft.preSharedKey, "pre_shared_key");
    } catch (error) {
      issues.push(issue("/preSharedKey", "WG_FORM_PRE_SHARED_KEY_INVALID", error instanceof Error ? error.message : "Invalid pre_shared_key."));
    }
  }

  const listenPort = parseListenPort(draft.listenPort);
  if (listenPort === undefined || listenPort < 1 || listenPort > 65535) {
    issues.push(issue("/listenPort", "WG_FORM_LISTEN_PORT_INVALID", "Listen port must be an integer between 1 and 65535."));
  }

  draft.address.forEach((address, index) => {
    try {
      normalizeIpInterface(address);
    } catch (error) {
      issues.push(issue(`/address/${index}`, "WG_FORM_ADDRESS_INVALID", error instanceof Error ? error.message : "Invalid address entry."));
    }
  });

  try {
    assertPlainExtra(draft.extra);
  } catch (error) {
    issues.push(issue("/extra", "WG_FORM_EXTRA_INVALID", error instanceof Error ? error.message : "Invalid extra fields."));
  }

  return issues;
}

export function createWireGuardCoreConfigFromDraft(draft: WireGuardCoreDraft): WireGuardCoreConfig {
  const issues = validateWireGuardCoreDraft(draft);
  if (issues.length > 0) {
    const firstIssue = issues[0]!;
    throw new Error(`${firstIssue.path}: ${firstIssue.message}`);
  }
  return createWireGuardCoreConfig(draftToOptions(draft));
}

export function generateWireGuardCoreConfigJsonFromDraft(draft: WireGuardCoreDraft, space = 2): string {
  return JSON.stringify(createWireGuardCoreConfigFromDraft(draft), null, space);
}

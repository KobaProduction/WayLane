export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonObject | JsonValue[];
export type JsonObject = { readonly [key: string]: JsonValue };

export type WireGuardCoreConfig = JsonObject & {
  readonly interface_name: string;
  readonly private_key: string;
  readonly pre_shared_key?: string;
  readonly listen_port: number;
  readonly address: readonly string[];
};

export type WireGuardKeyPair = {
  readonly privateKey: string;
  readonly publicKey: string;
};

export type CreateWireGuardCoreConfigOptions = {
  readonly interfaceName?: string;
  readonly privateKey?: string;
  readonly preSharedKey?: string;
  readonly listenPort?: number;
  readonly address?: readonly string[];
  readonly extra?: Record<string, JsonValue>;
};

export type GenerateWireGuardCoreConfigJsonOptions = CreateWireGuardCoreConfigOptions & {
  readonly space?: number;
};

export type WireGuardCorePayload = {
  readonly name: string;
  readonly type: "wg";
  readonly config: WireGuardCoreConfig;
  readonly exclude_inbound_tags: readonly string[];
  readonly fallbacks_inbound_tags: readonly string[];
};

export type CreateWireGuardCorePayloadOptions = CreateWireGuardCoreConfigOptions & {
  readonly name?: string;
};

export type WireGuardValidationIssue = {
  readonly code: string;
  readonly path: string;
  readonly message: string;
};

export type WireGuardValidationResult =
  | {
      readonly ok: true;
      readonly config: WireGuardCoreConfig;
      readonly issues: readonly [];
    }
  | {
      readonly ok: false;
      readonly issues: readonly WireGuardValidationIssue[];
    };

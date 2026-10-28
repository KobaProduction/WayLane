export type CoreKind = "xray" | "wg";

export type CoreKitValidationIssue = {
  readonly code: string;
  readonly path: string;
  readonly message: string;
  readonly severity?: "error" | "warning" | "info";
};

export type CoreKitValidationResult<T = unknown> =
  | {
      readonly ok: true;
      readonly config: T;
      readonly issues: readonly [];
    }
  | {
      readonly ok: false;
      readonly issues: readonly CoreKitValidationIssue[];
    };

export type CoreConfigTemplateResult = {
  readonly kind: CoreKind;
  readonly configJson: string;
  readonly generated?: Record<string, unknown>;
};

export type CoreKitCapabilities = {
  readonly coreConfigTemplate: boolean;
  readonly rawConfigValidation: boolean;
  readonly keyGeneration: boolean;
  readonly formDrafts: boolean;
  readonly clientLinks: boolean;
};

export type CoreKitValidationOptions = {
  readonly xrayVersion?: string;
};

export type CoreKit<T = unknown> = {
  readonly kind: CoreKind;
  readonly label: string;
  readonly browserSafe: true;
  readonly capabilities: CoreKitCapabilities;
  createDefaultConfigJson(): CoreConfigTemplateResult;
  validateConfig(input: unknown, options?: CoreKitValidationOptions): CoreKitValidationResult<T>;
};

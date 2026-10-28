# PasarGuard Core Kit

Browser-safe TypeScript registry facade for PasarGuard core configuration packages.

## Package

```ts
import {
  createCoreConfigTemplate,
  getCoreKit,
  validateCoreConfig
} from "@pasarguard/core-kit";

const template = createCoreConfigTemplate("xray");
const result = validateCoreConfig("xray", template.configJson);
```

## Direct Imports

```ts
import { createDefaultXrayCoreConfigJson } from "@pasarguard/core-kit/xray/generators";
import { generateWireGuardKeyPair } from "@pasarguard/core-kit/wireguard";
```

## Commands

```powershell
bun install
bun run typecheck
bun run build
bun test
```

## Supported Core Kinds

- `xray`
- `wg`

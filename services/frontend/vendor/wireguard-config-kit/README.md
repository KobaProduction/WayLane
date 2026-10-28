# PasarGuard WireGuard Config Kit

Browser-safe TypeScript helpers for generating and validating WireGuard core configuration JSON.

## Package

```ts
import {
  createDefaultWireGuardCoreDraft,
  generateWireGuardCoreConfigJson,
  generateWireGuardKeyPair,
  validateWireGuardCoreConfig
} from "@pasarguard/wireguard-config-kit";
```

## Commands

```powershell
bun install
bun run typecheck
bun run build
bun test
```

## Core Shape

Generated configs target PasarGuard WireGuard cores:

```json
{
  "interface_name": "wg0",
  "private_key": "<generated>",
  "listen_port": 51820,
  "address": ["10.0.0.1/8"]
}
```

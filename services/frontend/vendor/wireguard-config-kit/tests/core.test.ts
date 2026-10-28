import { describe, expect, it } from "bun:test";
import {
  createDefaultWireGuardCoreDraft,
  createWireGuardCorePayload,
  createWireGuardCoreConfig,
  createWireGuardCoreConfigFromDraft,
  generateWireGuardCoreConfigJson,
  generateWireGuardCoreConfigJsonFromDraft,
  generateWireGuardKeyPair,
  getWireGuardCoreFieldVisibility,
  getWireGuardCoreFormCapabilities,
  getWireGuardPublicKey,
  syncWireGuardCoreDraftPublicKey,
  validateWireGuardCoreDraft,
  validateWireGuardCoreConfig
} from "../src/index.js";

const knownPrivateKey = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=";
const knownPublicKey = "L+V9o0fNYkMVKNqsX7spBzD/9oSvxM/C7ZCZX1jLO3Q=";
const knownPreSharedKey = "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE=";

describe("wireguard-config-kit", () => {
  it("creates default WireGuard core config JSON", () => {
    const config = createWireGuardCoreConfig({ privateKey: knownPrivateKey });

    expect(config).toEqual({
      interface_name: "wg0",
      private_key: knownPrivateKey,
      listen_port: 51820,
      address: ["10.0.0.1/8"]
    });
    expect(config).not.toHaveProperty("public_key");

    const json = generateWireGuardCoreConfigJson({ privateKey: knownPrivateKey });
    expect(JSON.parse(json)).toEqual(config);
  });

  it("generates valid key pairs and derives public keys from private keys", () => {
    const keyPair = generateWireGuardKeyPair();

    expect(validateWireGuardCoreConfig({
      interface_name: "wg0",
      private_key: keyPair.privateKey,
      listen_port: 51820,
      address: ["10.0.0.1/8"]
    }).ok).toBe(true);
    expect(getWireGuardPublicKey(keyPair.privateKey)).toBe(keyPair.publicKey);
    expect(getWireGuardPublicKey(knownPrivateKey)).toBe(knownPublicKey);
  });

  it("rejects invalid interface names, ports, keys, and addresses", () => {
    const valid = {
      interface_name: "wg0",
      private_key: knownPrivateKey,
      listen_port: 51820,
      address: ["10.0.0.1/8"]
    };

    expect(validateWireGuardCoreConfig({ ...valid, interface_name: "" }).ok).toBe(false);
    expect(validateWireGuardCoreConfig({ ...valid, interface_name: "wg,0" }).ok).toBe(false);
    expect(validateWireGuardCoreConfig({ ...valid, interface_name: "wg<=>0" }).ok).toBe(false);
    expect(validateWireGuardCoreConfig({ ...valid, listen_port: 0 }).ok).toBe(false);
    expect(validateWireGuardCoreConfig({ ...valid, listen_port: 65536 }).ok).toBe(false);
    expect(validateWireGuardCoreConfig({ ...valid, listen_port: 51820.5 }).ok).toBe(false);
    expect(validateWireGuardCoreConfig({ ...valid, private_key: "not-a-key" }).ok).toBe(false);
    expect(validateWireGuardCoreConfig({ ...valid, address: ["10.0.0.1/33"] }).ok).toBe(false);
    expect(validateWireGuardCoreConfig({ ...valid, address: ["999.0.0.1/8"] }).ok).toBe(false);
  });

  it("accepts an empty address list", () => {
    const config = createWireGuardCoreConfig({
      privateKey: knownPrivateKey,
      address: []
    });

    expect(config.address).toEqual([]);
  });

  it("validates and preserves optional pre_shared_key", () => {
    const config = createWireGuardCoreConfig({
      privateKey: knownPrivateKey,
      preSharedKey: knownPreSharedKey
    });

    expect(config.pre_shared_key).toBe(knownPreSharedKey);
    expect(validateWireGuardCoreConfig({ ...config, pre_shared_key: "bad" }).ok).toBe(false);
  });

  it("preserves extra JSON fields for forward compatibility", () => {
    const config = createWireGuardCoreConfig({
      privateKey: knownPrivateKey,
      extra: {
        peer_keepalive_seconds: 25,
        metadata: {
          owner: "panel"
        }
      }
    });

    expect(config.peer_keepalive_seconds).toBe(25);
    expect(config.metadata).toEqual({ owner: "panel" });
  });

  it("creates a WireGuard core payload for /api/core", () => {
    const payload = createWireGuardCorePayload({
      name: "wg-core",
      privateKey: knownPrivateKey
    });

    expect(payload).toMatchObject({
      name: "wg-core",
      type: "wg",
      exclude_inbound_tags: [],
      fallbacks_inbound_tags: [],
      config: {
        interface_name: "wg0",
        private_key: knownPrivateKey,
        listen_port: 51820,
        address: ["10.0.0.1/8"]
      }
    });
  });

  it("creates frontend form drafts with derived public keys", () => {
    const draft = createDefaultWireGuardCoreDraft({ privateKey: knownPrivateKey });

    expect(draft).toMatchObject({
      interfaceName: "wg0",
      privateKey: knownPrivateKey,
      publicKey: knownPublicKey,
      preSharedKey: "",
      listenPort: 51820,
      address: ["10.0.0.1/8"],
      extra: {}
    });

    const generated = createDefaultWireGuardCoreDraft();
    expect(generated.privateKey).toBeTruthy();
    expect(generated.publicKey).toBe(getWireGuardPublicKey(generated.privateKey));

    const emptyPrivateKey = createDefaultWireGuardCoreDraft({ privateKey: "" });
    expect(emptyPrivateKey.publicKey).toBe("");
  });

  it("exposes form capabilities and field visibility", () => {
    const capabilities = getWireGuardCoreFormCapabilities();

    expect(capabilities.fieldOrder).toEqual([
      "interfaceName",
      "privateKey",
      "publicKey",
      "preSharedKey",
      "listenPort",
      "address",
      "extra"
    ]);
    expect(capabilities.fields.privateKey).toMatchObject({
      configKey: "private_key",
      input: "secret",
      required: true
    });
    expect(capabilities.fields.publicKey).toMatchObject({
      input: "readonly",
      readOnly: true
    });
    expect(getWireGuardCoreFieldVisibility(createDefaultWireGuardCoreDraft({ privateKey: knownPrivateKey }), capabilities)).toEqual({
      interfaceName: true,
      privateKey: true,
      publicKey: true,
      preSharedKey: true,
      listenPort: true,
      address: true,
      extra: true
    });
  });

  it("validates and compiles form drafts", () => {
    const draft = createDefaultWireGuardCoreDraft({
      interfaceName: " wg1 ",
      privateKey: knownPrivateKey,
      listenPort: "51821",
      address: ["10.8.0.1/24"],
      extra: { peer_keepalive_seconds: 25 }
    });

    expect(validateWireGuardCoreDraft(draft)).toEqual([]);
    expect(createWireGuardCoreConfigFromDraft(draft)).toEqual({
      interface_name: "wg1",
      private_key: knownPrivateKey,
      listen_port: 51821,
      address: ["10.8.0.1/24"],
      peer_keepalive_seconds: 25
    });
    expect(JSON.parse(generateWireGuardCoreConfigJsonFromDraft(draft))).toMatchObject({
      interface_name: "wg1",
      listen_port: 51821
    });
  });

  it("reports field-level draft validation issues", () => {
    const draft = createDefaultWireGuardCoreDraft({
      interfaceName: "wg,0",
      privateKey: "bad",
      preSharedKey: "bad",
      listenPort: "nope",
      address: ["10.0.0.1/33"]
    });
    const issues = validateWireGuardCoreDraft(draft);

    expect(issues.map((issue) => issue.path)).toEqual([
      "/interfaceName",
      "/privateKey",
      "/preSharedKey",
      "/listenPort",
      "/address/0"
    ]);
  });

  it("syncs readonly public key display from edited private keys", () => {
    const draft = createDefaultWireGuardCoreDraft({
      privateKey: "bad",
      publicKey: "stale"
    });

    expect(syncWireGuardCoreDraftPublicKey(draft).publicKey).toBe("");
    expect(syncWireGuardCoreDraftPublicKey({ ...draft, privateKey: knownPrivateKey }).publicKey).toBe(knownPublicKey);
  });
});

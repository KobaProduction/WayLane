import { afterEach, describe, expect, test } from "bun:test";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";

let tempDir: string | undefined;

afterEach(async () => {
  if (tempDir) {
    await rm(tempDir, { recursive: true, force: true });
    tempDir = undefined;
  }
});

describe("browser-safe bundle", () => {
  test("bundles root facade without backend-only Xray node helpers", async () => {
    const tempRoot = join(process.cwd(), "tests", ".tmp");
    await mkdir(tempRoot, { recursive: true });
    tempDir = await mkdtemp(join(tempRoot, "browser-smoke-"));
    const entrypoint = join(tempDir, "entry.ts");
    await writeFile(
      entrypoint,
      [
        'import { createCoreConfigTemplate, validateCoreConfig } from "@waylane/core-kit";',
        'import { createDefaultXrayCoreConfigJson } from "@waylane/core-kit/xray/generators";',
        'import { generateWireGuardKeyPair } from "@waylane/core-kit/wireguard";',
        'const template = createCoreConfigTemplate("xray");',
        'const validation = validateCoreConfig("xray", template.configJson);',
        "console.log(template.kind, validation.ok, createDefaultXrayCoreConfigJson().length, generateWireGuardKeyPair().publicKey.length);"
      ].join("\n")
    );

    const result = await Bun.build({
      entrypoints: [entrypoint],
      format: "esm",
      target: "browser",
      write: false
    });

    if (!result.success) {
      throw new Error(result.logs.map((log) => log.message).join("\n"));
    }

    const bundled = (await Promise.all(result.outputs.map((output) => output.text()))).join("\n");
    expect(bundled).not.toContain("testXrayConfig");
    expect(bundled).not.toContain("findXrayBinary");
    expect(bundled).not.toContain("pasarguard-xray-config-kit-");
  });
});

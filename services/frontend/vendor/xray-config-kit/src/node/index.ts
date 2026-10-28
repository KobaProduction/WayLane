import { access, mkdtemp, rm, writeFile } from "node:fs/promises";
import { constants } from "node:fs";
import { tmpdir } from "node:os";
import { delimiter, join } from "node:path";
import { spawn } from "node:child_process";
import type { JsonObject, XrayConfig } from "../core/types.js";

export type XrayBinaryTestOptions = {
  readonly binaryPath?: string;
  readonly timeoutMs?: number;
  readonly keepTempFile?: boolean;
};

export type XrayBinaryTestResult = {
  readonly ok: boolean;
  readonly binaryPath: string;
  readonly exitCode: number | null;
  readonly stdout: string;
  readonly stderr: string;
  readonly configPath: string;
};

function commandExists(command: string): Promise<boolean> {
  return access(command, constants.X_OK).then(() => true, () => false);
}

function run(command: string, args: readonly string[], timeoutMs: number): Promise<{ exitCode: number | null; stdout: string; stderr: string }> {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { windowsHide: true });
    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => {
      child.kill();
      reject(new Error(`Timed out after ${timeoutMs}ms`));
    }, timeoutMs);

    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });
    child.on("error", (error) => {
      clearTimeout(timer);
      reject(error);
    });
    child.on("close", (exitCode) => {
      clearTimeout(timer);
      resolve({ exitCode, stdout, stderr });
    });
  });
}

export async function findXrayBinary(candidates: readonly string[] = []): Promise<string | undefined> {
  const explicit = process.env.XRAY_BINARY ? [process.env.XRAY_BINARY] : [];
  const names = process.platform === "win32" ? ["xray.exe", "xray"] : ["xray"];
  const pathCandidates = (process.env.PATH ?? "")
    .split(delimiter)
    .flatMap((entry) => names.map((name) => join(entry, name)));
  for (const candidate of [...candidates, ...explicit, ...names, ...pathCandidates]) {
    if (await commandExists(candidate)) return candidate;
  }
  return undefined;
}

export async function getXrayVersion(binaryPath?: string): Promise<string | undefined> {
  const binary = binaryPath ?? await findXrayBinary();
  if (!binary) return undefined;
  const result = await run(binary, ["version"], 10_000);
  return result.stdout.split(/\r?\n/)[0]?.trim() || result.stderr.split(/\r?\n/)[0]?.trim() || undefined;
}

export async function testXrayConfig(config: XrayConfig | JsonObject, options: XrayBinaryTestOptions = {}): Promise<XrayBinaryTestResult> {
  const binaryPath = options.binaryPath ?? await findXrayBinary();
  if (!binaryPath) {
    throw new Error("Xray binary not found. Pass binaryPath or set XRAY_BINARY.");
  }

  const tempDir = await mkdtemp(join(tmpdir(), "waylane-xray-config-kit-"));
  const configPath = join(tempDir, "config.json");
  await writeFile(configPath, JSON.stringify(config, null, 2), "utf8");

  try {
    const result = await run(binaryPath, ["run", "-test", "-format", "json", "-config", configPath], options.timeoutMs ?? 30_000);
    return {
      ok: result.exitCode === 0,
      binaryPath,
      exitCode: result.exitCode,
      stdout: result.stdout,
      stderr: result.stderr,
      configPath
    };
  } finally {
    if (!options.keepTempFile) await rm(tempDir, { recursive: true, force: true });
  }
}

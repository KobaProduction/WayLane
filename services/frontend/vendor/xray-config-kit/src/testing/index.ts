import { buildXrayConfig } from "../core/compiler.js";
import { stableStringify } from "../core/json.js";
import type { BuildOptions, Profile } from "../core/types.js";

export function buildGoldenConfig(profile: Profile, options: BuildOptions = {}): string {
  return stableStringify(buildXrayConfig(profile, { ...options, mode: options.mode ?? "permissive" }).config);
}

export function createFixtureProfile(profile: Profile): Profile {
  return profile;
}

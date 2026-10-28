import { profileSchema } from "../schemas/profile.js";
import { createProfile } from "../core/profile.js";
import type { Profile } from "../core/types.js";

export function migrateProfile(input: unknown, options: { readonly toSchemaVersion?: "xck.v1" } = {}): Profile {
  const toSchemaVersion = options.toSchemaVersion ?? "xck.v1";
  if (toSchemaVersion !== "xck.v1") {
    throw new Error(`Unsupported target schema version: ${toSchemaVersion}`);
  }

  const parsed = profileSchema.safeParse(input);
  if (parsed.success) return parsed.data as Profile;

  if (typeof input === "object" && input !== null) {
    const candidate = input as Partial<Profile>;
    return createProfile({
      ...candidate,
      schemaVersion: "xck.v1",
      inbounds: candidate.inbounds ?? [],
      includeDefaultPolicy: false
    });
  }

  throw new Error("Cannot migrate a non-object profile.");
}

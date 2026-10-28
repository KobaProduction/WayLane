import type { Issue } from "./types.js";

export type Result<T, E = Issue> =
  | {
      readonly ok: true;
      readonly value: T;
      readonly warnings: Issue[];
    }
  | {
      readonly ok: false;
      readonly errors: E[];
      readonly warnings: Issue[];
    };

export function ok<T>(value: T, warnings: Issue[] = []): Result<T> {
  return { ok: true, value, warnings };
}

export function err<E = Issue>(errors: E[], warnings: Issue[] = []): Result<never, E> {
  return { ok: false, errors, warnings };
}

export function issuesToResult<T>(value: T, issues: readonly Issue[]): Result<T> {
  const errors = issues.filter((issue) => issue.severity === "error");
  const warnings = issues.filter((issue) => issue.severity !== "error");
  return errors.length > 0 ? err(errors, warnings) : ok(value, warnings);
}

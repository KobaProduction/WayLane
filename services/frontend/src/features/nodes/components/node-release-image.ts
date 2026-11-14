/** A trusted, operator-configured OCI repository and an immutable digest. */
export const OFFICIAL_NODE_REPOSITORY = 'ghcr.io/kobaproduction/waylane-node'

export function withNodeReleaseRepository(repository: string): string {
  // Each registry has its OWN immutable digest; do not transplant a GHCR digest.
  return `${repository}@sha256:`
}

export function approvedNodeReleaseImage(image: string, repositories: string[]): boolean {
  return repositories.some(repository => image.startsWith(`${repository}@sha256:`) &&
    /^[0-9a-f]{64}$/.test(image.slice(repository.length + 8)))
}

/** A trusted, operator-configured OCI repository and an immutable digest. */
export const OFFICIAL_NODE_REPOSITORY = 'ghcr.io/kobaproduction/waylane-node'

export function withNodeReleaseRepository(repository: string, previousImage: string): string {
  const digest = previousImage.split('@sha256:')[1] ?? ''
  return `${repository}@sha256:${digest}`
}

export function approvedNodeReleaseImage(image: string, repositories: string[]): boolean {
  return repositories.some(repository => image.startsWith(`${repository}@sha256:`) &&
    /^[0-9a-f]{64}$/.test(image.slice(repository.length + 8)))
}

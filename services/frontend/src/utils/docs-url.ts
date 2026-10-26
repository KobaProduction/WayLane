import { DOCUMENTATION } from '@/constants/Project'

/** Documentation currently lives in the WayLane repository's README.
 * Route-specific guides are not published yet; avoid linking to the old vendor's docs.
 */
export function getDocsUrl(_pagePath: string): string {
  return DOCUMENTATION
}

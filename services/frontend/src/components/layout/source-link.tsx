import { REPO_URL } from '@/constants/Project'

/** Link to the WayLane source repository; not a GitHub star widget. */
export const SourceLink = () => (
  <a href={REPO_URL} target="_blank" rel="noopener noreferrer" className="text-muted-foreground hover:text-foreground text-xs underline-offset-4 hover:underline">
    WayLane source
  </a>
)

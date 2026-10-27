import { FC } from 'react'

const FooterContent = () => (
  <p className="text-muted-foreground inline-block flex-grow text-center text-xs">
    WayLane
  </p>
)

export const Footer: FC = ({ ...props }) => (
  <div className="relative flex w-full pt-1 pb-3" {...props}>
    <FooterContent />
  </div>
)

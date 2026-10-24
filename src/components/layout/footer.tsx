import { REPO_URL } from '@/constants/Project'
import { FC } from 'react'

const FooterContent = () => {
  return (
    <p className="text-muted-foreground inline-block flex-grow text-center text-xs">
      Maintained by &nbsp;
      <a className="text-primary hover:underline" href={REPO_URL}>
        Intens
      </a>
    </p>
  )
}

export const Footer: FC = ({ ...props }) => {
  return (
    <div className="relative flex w-full pt-1 pb-3" {...props}>
      <FooterContent />
    </div>
  )
}

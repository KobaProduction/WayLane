import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { Button } from '@/components/ui/button'
import { Copy, EllipsisVertical, Pen, Power, PowerOff, Trash2 } from 'lucide-react'
import useDirDetection from '@/hooks/use-dir-detection'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'
import { cn } from '@/lib/utils'
import { toast } from 'sonner'
import { createSubscriptionTemplate, useRemoveSubscriptionTemplate, SubscriptionTemplateCreate, SubscriptionTemplateResponse } from '@/service/api'
import { queryClient } from '@/utils/query-client'
import { translateHtml } from '@/utils/html-translation'

interface SubscriptionTemplateActionsMenuProps {
  template: SubscriptionTemplateResponse
  onEdit: (template: SubscriptionTemplateResponse) => void
  onToggleStatus: (template: SubscriptionTemplateResponse) => void
  canCreate?: boolean
  canUpdate?: boolean
  canDelete?: boolean
  className?: string
}

const DeleteAlertDialog = ({ subscriptionTemplate, isOpen, onClose, onConfirm }: { subscriptionTemplate: SubscriptionTemplateResponse; isOpen: boolean; onClose: () => void; onConfirm: () => void }) => {
  const { t } = useTranslation()
  const dir = useDirDetection()

  return (
    <AlertDialog open={isOpen} onOpenChange={onClose}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{t('templates.deleteSubscriptionTemplateTitle')}</AlertDialogTitle>
          <AlertDialogDescription>
            <span dir={dir} dangerouslySetInnerHTML={{ __html: translateHtml(t, 'templates.deleteSubscriptionTemplatePrompt', { name: subscriptionTemplate.name }) }} />
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel onClick={onClose}>{t('cancel')}</AlertDialogCancel>
          <AlertDialogAction variant="destructive" onClick={onConfirm}>
            {t('remove')}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

export default function SubscriptionTemplateActionsMenu({ template, onEdit, onToggleStatus, canCreate = true, canUpdate = true, canDelete = true, className }: SubscriptionTemplateActionsMenuProps) {
  const { t } = useTranslation()
  const dir = useDirDetection()
  const removeSubscriptionTemplateMutation = useRemoveSubscriptionTemplate()
  const [isDeleteDialogOpen, setDeleteDialogOpen] = useState(false)

  const handleDeleteClick = (event: Event) => {
    event.preventDefault()
    event.stopPropagation()
    if (!canDelete) return
    setDeleteDialogOpen(true)
  }

  const handleConfirmDelete = async () => {
    if (!canDelete) return

    try {
      await removeSubscriptionTemplateMutation.mutateAsync({
        templateId: template.id,
      })
      toast.success(t('success', { defaultValue: 'Success' }), {
        description: t('templates.deleteSuccess', {
          name: template.name,
          defaultValue: 'Template «{name}» has been deleted successfully',
        }),
      })
      setDeleteDialogOpen(false)
      queryClient.invalidateQueries({ queryKey: ['/api/subscription_templates'] })
    } catch (error) {
      toast.error(t('error', { defaultValue: 'Error' }), {
        description: t('templates.deleteFailed', {
          name: template.name,
          defaultValue: 'Failed to delete template «{name}»',
        }),
      })
    }
  }

  const handleDuplicate = async () => {
    if (!canCreate) return

    try {
      const newTemplate: SubscriptionTemplateCreate = {
        ...template,
        name: `${template.name} (copy)`,
      }
      await createSubscriptionTemplate(newTemplate)
      toast.success(t('success', { defaultValue: 'Success' }), {
        description: t('templates.duplicateSuccess', {
          name: template.name,
          defaultValue: 'Template «{name}» has been duplicated successfully',
        }),
      })
      queryClient.invalidateQueries({ queryKey: ['/api/subscription_templates'] })
    } catch (error) {
      toast.error(t('error', { defaultValue: 'Error' }), {
        description: t('templates.duplicateFailed', {
          name: template.name,
          defaultValue: 'Failed to duplicate template «{name}»',
        }),
      })
    }
  }

  if (!canCreate && !canUpdate && !canDelete) return null

  return (
    <div className={cn(className)} onClick={e => e.stopPropagation()}>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button type="button" variant="ghost" size="icon" className="template-dropdown-menu">
            <EllipsisVertical />
            <span className="sr-only">Template Actions</span>
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align={dir === 'rtl' ? 'end' : 'start'} className="template-dropdown-menu">
          {canUpdate && (
            <>
              <DropdownMenuItem
                onSelect={e => {
                  e.stopPropagation()
                  onEdit(template)
                }}
              >
                <Pen className="h-4 w-4" />
                <span>{t('edit')}</span>
              </DropdownMenuItem>
              <DropdownMenuItem
                onSelect={e => {
                  e.stopPropagation()
                  onToggleStatus(template)
                }}
              >
                {template.is_disabled ? <Power className="h-4 w-4" /> : <PowerOff className="h-4 w-4" />}
                {template.is_disabled ? t('enable') : t('disable')}
              </DropdownMenuItem>
            </>
          )}
          {canCreate && (
            <DropdownMenuItem
              dir={dir}
              className="template-dropdown-menu flex items-center"
              onSelect={e => {
                e.stopPropagation()
                handleDuplicate()
              }}
            >
              <Copy className="h-4 w-4" />
              <span>{t('duplicate')}</span>
            </DropdownMenuItem>
          )}
          {(canUpdate || canCreate) && canDelete && <DropdownMenuSeparator />}
          {canDelete && (
            <DropdownMenuItem
              dir={dir}
              className="template-dropdown-menu flex items-center !text-red-500"
              onSelect={e => {
                e.stopPropagation()
                handleDeleteClick(e)
              }}
            >
              <Trash2 className="h-4 w-4 text-red-500" />
              <span>{t('delete')}</span>
            </DropdownMenuItem>
          )}
        </DropdownMenuContent>
      </DropdownMenu>

      {canDelete && <DeleteAlertDialog subscriptionTemplate={template} isOpen={isDeleteDialogOpen} onClose={() => setDeleteDialogOpen(false)} onConfirm={handleConfirmDelete} />}
    </div>
  )
}

import { Link } from 'react-router'
import { SubscriptionFormActions } from '@/features/subscriptions/components/subscription-form-actions'

import { Form, FormControl, FormDescription, FormField, FormItem, FormLabel, FormMessage } from '@/components/ui/form'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Separator } from '@/components/ui/separator'
import { Skeleton } from '@/components/ui/skeleton'
import { DEFAULT_SHADOWSOCKS_METHOD } from '@/constants/Proxies'
import { OnHoldTimeoutAction, ShadowsocksMethods, useGetGeneralSettings } from '@/service/api'

import { zodResolver } from '@hookform/resolvers/zod'

import { useMemo } from 'react'
import { useForm } from 'react-hook-form'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { z } from 'zod'
import { useSettingsContext } from './_dashboard.settings'

// general settings validation schema
const generalSettingsSchema = z.object({
  default_method: z.string().default(''),
  on_hold_timeout_action: z.nativeEnum(OnHoldTimeoutAction).default(OnHoldTimeoutAction.activate),
})

type GeneralSettingsFormInput = z.input<typeof generalSettingsSchema>

export default function General() {
  const { t } = useTranslation()
  const { isLoading, error, updateSettings, isSaving } = useSettingsContext()
  const { data: generalSettings, isLoading: isGeneralLoading, error: generalError } = useGetGeneralSettings()



  const generalFormValues = useMemo<GeneralSettingsFormInput>(
    () =>
      generalSettings
        ? {
            default_method: generalSettings.default_method || DEFAULT_SHADOWSOCKS_METHOD,
            on_hold_timeout_action: generalSettings.on_hold_timeout_action || OnHoldTimeoutAction.activate,
          }
        : {
            default_method: '',
            on_hold_timeout_action: OnHoldTimeoutAction.activate,
          },
    [generalSettings?.default_method, generalSettings?.on_hold_timeout_action],
  )

  const form = useForm<GeneralSettingsFormInput>({
    resolver: zodResolver(generalSettingsSchema),
    values: generalFormValues,
  })

  const onSubmit = async (data: GeneralSettingsFormInput) => {
    try {
      // Filter out empty values and prepare the payload
      const filteredData: any = {
        general: {
          default_method: data.default_method || DEFAULT_SHADOWSOCKS_METHOD,
          on_hold_timeout_action: data.on_hold_timeout_action || OnHoldTimeoutAction.activate,
        },
      }

      await updateSettings(filteredData)
    } catch (error) {
      // Error handling is done in the parent context
    }
  }

  const handleCancel = () => {
    if (!generalSettings) return
    form.reset({
      default_method: generalSettings.default_method || DEFAULT_SHADOWSOCKS_METHOD,
      on_hold_timeout_action: generalSettings.on_hold_timeout_action || OnHoldTimeoutAction.activate,
    })
    toast.success(t('settings.general.cancelSuccess'))
  }

  const loadError = error || generalError

  // TODO: skeleton needs to be improved
  if (isLoading || isGeneralLoading) {
    return (
      <div className="w-full p-4 sm:py-6 lg:py-8">
        <div className="space-y-6 sm:space-y-8 lg:space-y-10">
          {/* General Settings Skeleton */}
          <div className="space-y-4">
            <div className="space-y-2">
              <Skeleton className="h-6 w-48" />
              <Skeleton className="h-4 w-96" />
            </div>
            <Skeleton className="h-16" />
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              {[...Array(4)].map((_, i) => (
                <div key={i} className="space-y-2">
                  <Skeleton className="h-4 w-24" />
                  <Skeleton className="h-10" />
                  <Skeleton className="h-3 w-64" />
                </div>
              ))}
            </div>
            <Skeleton className="h-16" />
          </div>

          {/* Action Buttons Skeleton */}
          <div className="flex flex-col gap-3 pt-4 sm:flex-row sm:gap-4">
            <div className="flex-1"></div>
            <div className="flex flex-col gap-3 sm:shrink-0 sm:flex-row sm:gap-4">
              <Skeleton className="h-10 w-24" />
              <Skeleton className="h-10 w-20" />
            </div>
          </div>
        </div>
      </div>
    )
  }

  if (loadError) {
    return (
      <div className="flex min-h-[400px] items-center justify-center p-4 sm:py-6 lg:py-8">
        <div className="space-y-3 text-center">
          <div className="text-lg text-red-500">⚠️</div>
          <p className="text-sm text-red-500">Error loading settings</p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex min-h-[calc(100vh-200px)] w-full flex-col">
      <Form {...form}>
        <form onSubmit={form.handleSubmit(onSubmit)} className="flex flex-1 flex-col p-4 sm:py-6 lg:py-8">
          <div className="mb-4 sm:mb-6 lg:mb-8">
            {/* General Settings */}
            <div className="grid grid-cols-1 gap-3 sm:gap-4 md:grid-cols-2">
              <FormField
                control={form.control}
                name="default_method"
                render={({ field }) => (
                  <FormItem className="space-y-2">
                    <FormLabel className="flex items-center gap-2 text-xs font-medium sm:text-sm">{t('settings.general.defaultMethod.title')}</FormLabel>
                    <FormControl>
                      <Select value={field.value} onValueChange={field.onChange}>
                        <SelectTrigger className="text-xs sm:text-sm">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {Object.values(ShadowsocksMethods)
                            .filter(Boolean)
                            .map(flow => {
                              return (
                                <SelectItem value={flow} key={flow} className="text-xs sm:text-sm">
                                  {flow}
                                </SelectItem>
                              )
                            })}
                        </SelectContent>
                      </Select>
                    </FormControl>
                    <FormDescription className="text-muted-foreground text-xs sm:text-sm">{t('settings.general.defaultMethod.description')}</FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="on_hold_timeout_action"
                render={({ field }) => (
                  <FormItem className="space-y-2">
                    <FormLabel className="flex items-center gap-2 text-xs font-medium sm:text-sm">{t('settings.general.onHoldTimeoutAction.title')}</FormLabel>
                    <FormControl>
                      <Select value={field.value} onValueChange={field.onChange}>
                        <SelectTrigger className="text-xs sm:text-sm">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value={OnHoldTimeoutAction.activate} className="text-xs sm:text-sm">
                            {t('settings.general.onHoldTimeoutAction.activate')}
                          </SelectItem>
                          <SelectItem value={OnHoldTimeoutAction.disable} className="text-xs sm:text-sm">
                            {t('settings.general.onHoldTimeoutAction.disable')}
                          </SelectItem>
                          <SelectItem value={OnHoldTimeoutAction.delete} className="text-xs sm:text-sm">
                            {t('settings.general.onHoldTimeoutAction.delete')}
                          </SelectItem>
                        </SelectContent>
                      </Select>
                    </FormControl>
                    <FormDescription className="text-muted-foreground text-xs sm:text-sm">{t('settings.general.onHoldTimeoutAction.description')}</FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>
          </div>

          <Separator className="my-3" />

          <div className="flex flex-wrap items-center justify-between gap-3 py-3">
            <div>
              <h3 className="text-base font-semibold">Управление автономными Node</h3>
              <p className="text-muted-foreground text-sm">
                Перезапуск, конфигурация Xray и состояние WSS управляются отдельно для каждой Node.
                Глобальная команда PasarGuard для принудительного переподключения всех серверов удалена.
              </p>
            </div>
            <Link className="rounded border px-4 py-2 text-sm underline" to="/nodes">Открыть Node Registry</Link>
          </div>

          <SubscriptionFormActions onCancel={handleCancel} isSaving={isSaving} className="mt-auto sm:mt-auto" />
        </form>
      </Form>
    </div>
  )
}

import { DataLimitResetStrategy, ShadowsocksMethods, SubscriptionStatusCreate } from '@/service/api'
import { zodResolver } from '@hookform/resolvers/zod'
import type { TFunction } from 'i18next'
import type { FieldError, FieldErrors, Resolver } from 'react-hook-form'
import { z } from 'zod'

const MAX_ON_HOLD_EXPIRE_DURATION_SECONDS = 2_147_483_647

const subscriptionTemplateFormObjectSchema = z.object({
  name: z.string().min(1, 'validation.required'),
  status: z.enum([SubscriptionStatusCreate.active, SubscriptionStatusCreate.on_hold]).default(SubscriptionStatusCreate.active),
  data_limit: z.number().min(0).optional(),
  hwid_limit: z.number().min(0).nullable().optional(),
  expire_duration: z.number().min(0).max(MAX_ON_HOLD_EXPIRE_DURATION_SECONDS).optional(),
  on_hold_timeout: z.number().optional(),
  method: z
    .enum([ShadowsocksMethods['aes-128-gcm'], ShadowsocksMethods['aes-256-gcm'], ShadowsocksMethods['chacha20-ietf-poly1305'], ShadowsocksMethods['xchacha20-poly1305']])
    .default(ShadowsocksMethods['chacha20-ietf-poly1305']),
  groups: z.array(z.number()).min(1, 'validation.required'),
  data_limit_reset_strategy: z
    .enum([
      DataLimitResetStrategy['month'],
      DataLimitResetStrategy['day'],
      DataLimitResetStrategy['week'],
      DataLimitResetStrategy['no_reset'],
      DataLimitResetStrategy['week'],
      DataLimitResetStrategy['year'],
    ])
    .optional(),
  reset_usages: z.boolean().optional(),
})

function refineSubscriptionTemplateOnHold(data: z.infer<typeof subscriptionTemplateFormObjectSchema>, ctx: z.RefinementCtx) {
  if (data.status !== SubscriptionStatusCreate.on_hold) return
  const exp = data.expire_duration
  if (exp == null || !Number.isFinite(Number(exp)) || Number(exp) <= 0) {
    ctx.addIssue({
      code: z.ZodIssueCode.custom,
      message: 'validation.required',
      path: ['expire_duration'],
    })
  }
}

export const subscriptionTemplateFormSchema = subscriptionTemplateFormObjectSchema.superRefine(refineSubscriptionTemplateOnHold)

export type SubscriptionTemplatesFromValueInput = z.input<typeof subscriptionTemplateFormSchema>
export type SubscriptionTemplatesFromValue = z.infer<typeof subscriptionTemplateFormSchema>

export const subscriptionTemplateFormDefaultValues: Partial<SubscriptionTemplatesFromValueInput> = {
  name: '',
  status: SubscriptionStatusCreate.active,
  data_limit: 0,
  hwid_limit: null,
  expire_duration: 0,
  method: ShadowsocksMethods['chacha20-ietf-poly1305'],
  on_hold_timeout: undefined,
  groups: [],
  reset_usages: false,
}

/** Wraps zodResolver: turns `validation.required` into fully translated messages (same idea as `user-modal` `setError` with `t('validation.required', { field: … })`). */
export function createSubscriptionTemplateFormResolver(t: TFunction): Resolver<SubscriptionTemplatesFromValueInput> {
  const base = zodResolver(subscriptionTemplateFormSchema)
  return async (values, context, options) => {
    const result = await base(values, context, options)
    if (!result.errors) return result
    const errors = { ...result.errors } as FieldErrors<SubscriptionTemplatesFromValueInput>
    for (const key of Object.keys(errors) as Array<keyof FieldErrors<SubscriptionTemplatesFromValueInput>>) {
      const err = errors[key] as FieldError | undefined
      if (err && typeof err === 'object' && err.message === 'validation.required') {
        const fieldName = String(key)
        const fieldLabelKey = fieldName === 'name' ? 'templates.name' : fieldName === 'expire_duration' ? 'templates.expire' : fieldName === 'groups' ? 'templates.groups' : `fields.${fieldName}`
        ;(errors as Record<string, FieldError | undefined>)[fieldName] = {
          ...err,
          message: t('validation.required', { field: t(fieldLabelKey, { defaultValue: fieldName }) }),
        }
      }
    }
    return { ...result, errors } as Awaited<ReturnType<typeof base>>
  }
}

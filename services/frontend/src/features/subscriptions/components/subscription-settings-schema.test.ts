import { describe, expect, it } from 'bun:test'

import { builtInVariableKeys, customVariablesSchema, defaultSubscriptionRules } from './subscription-settings-schema'

const matchDefaultRule = (userAgent: string) =>
  defaultSubscriptionRules.find(rule => new RegExp(rule.pattern).test(userAgent))?.target

describe('default subscription rules', () => {
  it('uses the first matching rule for built-in clients', () => {
    expect(matchDefaultRule('v2rayN/7.15')).toBe('links')
    expect(matchDefaultRule('v2rayNG/1.10')).toBe('links')
    expect(matchDefaultRule('Happ/2.0')).toBe('xray')
    expect(matchDefaultRule('Streisand/1.0')).toBe('xray')
    expect(matchDefaultRule('ktor-client/2.3')).toBe('xray')
  })

  it('keeps other clients on the catch-all and has no InHive rule', () => {
    expect(matchDefaultRule('InHive/1.0')).toBe('links_base64')
    expect(matchDefaultRule('unknown-client/1.0')).toBe('links_base64')
    expect(defaultSubscriptionRules.some(rule => /inhive/i.test(rule.pattern))).toBe(false)
  })
})


describe('subscription UUID format variables', () => {
  it('provides subscription UUID and does not offer retired subscriber usernames', () => {
    expect(builtInVariableKeys).toContain('SUBSCRIPTION_UUID')
    expect(builtInVariableKeys).not.toContain('USERNAME')
  })

  it('reserves SUBSCRIPTION_UUID while allowing UUID placeholders in custom values', () => {
    expect(customVariablesSchema.safeParse([{ key: 'SUBSCRIPTION_UUID', value: 'override' }]).success).toBe(false)
    expect(customVariablesSchema.safeParse([{ key: 'CUSTOM_HOST', value: '{SUBSCRIPTION_UUID}.example.com' }]).success).toBe(true)
  })
})

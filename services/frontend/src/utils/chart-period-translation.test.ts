import { test } from 'node:test'
import assert from 'node:assert/strict'
import i18next from 'i18next'

import ru from '../../public/statics/locales/ru.json'
import en from '../../public/statics/locales/en.json'
import zh from '../../public/statics/locales/zh.json'
import fa from '../../public/statics/locales/fa.json'
import { formatChartPeriodQuantity } from './chart-period-utils'

const resources = {
  ru: { translation: ru },
  en: { translation: en },
  zh: { translation: zh },
  fa: { translation: fa },
}

test('dashboard subscription labels resolve in every supported locale', async () => {
  for (const lng of Object.keys(resources)) {
    const i18n = i18next.createInstance()
    await i18n.init({ lng, fallbackLng: false, resources, initImmediate: false })
    assert.notEqual(i18n.t('dashboardOverview.totalSubscriptions'), 'dashboardOverview.totalSubscriptions', lng)
    assert.notEqual(i18n.t('statistics.activeSubscriptions'), 'statistics.activeSubscriptions', lng)
  }
})

test('Russian chart period labels use correct singular, few, and many forms', async () => {
  const i18n = i18next.createInstance()
  await i18n.init({ lng: 'ru', fallbackLng: false, resources, initImmediate: false })
  for (const [count, unit, expected] of [
    [1, 'day', '1 день'], [2, 'day', '2 дня'], [5, 'day', '5 дней'],
    [7, 'day', '7 дней'], [11, 'day', '11 дней'], [21, 'day', '21 день'],
    [24, 'hour', '24 часа'], [12, 'hour', '12 часов'],
    [1, 'month', '1 месяц'], [3, 'month', '3 месяца'], [5, 'month', '5 месяцев'],
  ] as const) {
    assert.equal(formatChartPeriodQuantity(count, unit, i18n.t), expected)
  }
})

test('English chart period labels preserve natural number agreement', async () => {
  const i18n = i18next.createInstance()
  await i18n.init({ lng: 'en', fallbackLng: false, resources, initImmediate: false })
  assert.equal(formatChartPeriodQuantity(1, 'day', i18n.t), '1 day')
  assert.equal(formatChartPeriodQuantity(7, 'day', i18n.t), '7 days')
  assert.equal(formatChartPeriodQuantity(2, 'hour', i18n.t), '2 hours')
})

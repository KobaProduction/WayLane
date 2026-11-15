import i18n from 'i18next'
import LanguageDetector from 'i18next-browser-languagedetector'
import HttpApi from 'i18next-http-backend'
import { initReactI18next } from 'react-i18next'
import { joinURL } from 'ufo'

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .use(HttpApi)
  .init(
    {
      debug: import.meta.env.NODE_ENV === 'development',
      returnNull: false,
      fallbackLng: 'en',
      interpolation: {
        escapeValue: false,
      },
      react: {
        useSuspense: true,
      },
      load: 'languageOnly',
      detection: {
        caches: ['localStorage'],
      },
      backend: {
        loadPath: joinURL(import.meta.env.BASE_URL, `statics/locales/{{lng}}.json`),
        // Locale JSON keeps a stable URL between releases. Ignore any legacy
        // immutable HTTP cache so newly deployed UI strings are never shown
        // as untranslated i18next keys after a frontend-only update.
        requestOptions: { mode: 'cors', credentials: 'same-origin', cache: 'no-store' },
      },
    },
    function (err) {
      if (err) {
        console.error('i18next initialization error:', err)
      }
      const lang = i18n.language
      document.documentElement.lang = lang
      document.documentElement.setAttribute('dir', i18n.dir())
    },
  )

export default i18n

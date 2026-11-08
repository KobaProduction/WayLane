/** List vs grid for {@link CoreEditorDataTable} sections (inbounds, outbounds, routing, …). */
export type CoreEditorViewMode = 'list' | 'grid'

export const CORE_EDITOR_VIEW_MODE_STORAGE_KEY = 'view-mode:core-editor-lists'

export const DEFAULT_CORE_EDITOR_VIEW_MODE: CoreEditorViewMode = 'list'

const NUM_ADMINS_PER_PAGE_LOCAL_STORAGE_KEY = 'waylane-num-admins-per-page'
const NUM_ITEMS_PER_PAGE_DEFAULT = 10

const CHART_VIEW_TYPE_KEY = 'waylane-chart-view-type'

const CORES_LIST_USE_CONFIG_MODAL_KEY = 'waylane-cores-list-use-config-modal'
const DEFAULT_CORES_LIST_USE_CONFIG_MODAL = false

export const DATE_PICKER_PREFERENCE_KEY = 'waylane-date-picker-preference'
export type DatePickerPreference = 'locale' | 'gregorian' | 'persian'
const DEFAULT_DATE_PICKER_PREFERENCE: DatePickerPreference = 'locale'

export const CHART_VIEW_TYPE_CHANGE_EVENT = 'waylane-chart-view-type-change'
export type ChartViewType = 'bar' | 'area'
const DEFAULT_CHART_VIEW_TYPE: ChartViewType = 'bar'

export const getAdminsPerPageLimitSize = () => {
  const stored = typeof localStorage !== 'undefined' && localStorage.getItem(NUM_ADMINS_PER_PAGE_LOCAL_STORAGE_KEY)
  return parseInt(stored || '', 10) || NUM_ITEMS_PER_PAGE_DEFAULT
}

export const setAdminsPerPageLimitSize = (value: string) => {
  if (typeof localStorage !== 'undefined') localStorage.setItem(NUM_ADMINS_PER_PAGE_LOCAL_STORAGE_KEY, value)
}

export const getDatePickerPreference = (): DatePickerPreference => {
  if (typeof localStorage === 'undefined') return DEFAULT_DATE_PICKER_PREFERENCE
  const storedValue = localStorage.getItem(DATE_PICKER_PREFERENCE_KEY)
  if (storedValue === 'locale' || storedValue === 'gregorian' || storedValue === 'persian') {
    return storedValue
  }
  return DEFAULT_DATE_PICKER_PREFERENCE
}

export const setDatePickerPreference = (preference: DatePickerPreference) => {
  if (typeof localStorage === 'undefined') return
  localStorage.setItem(DATE_PICKER_PREFERENCE_KEY, preference)
}

export const getChartViewTypePreference = (): ChartViewType => {
  if (typeof localStorage === 'undefined') return DEFAULT_CHART_VIEW_TYPE
  const storedValue = localStorage.getItem(CHART_VIEW_TYPE_KEY)
  if (storedValue === 'bar' || storedValue === 'area') {
    return storedValue
  }
  return DEFAULT_CHART_VIEW_TYPE
}

export const setChartViewTypePreference = (viewType: ChartViewType) => {
  if (typeof localStorage === 'undefined') return
  localStorage.setItem(CHART_VIEW_TYPE_KEY, viewType)
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent<ChartViewType>(CHART_VIEW_TYPE_CHANGE_EVENT, { detail: viewType }))
  }
}

export const getCoresListUseConfigModal = (): boolean => {
  if (typeof localStorage === 'undefined') return DEFAULT_CORES_LIST_USE_CONFIG_MODAL
  return localStorage.getItem(CORES_LIST_USE_CONFIG_MODAL_KEY) === 'true'
}

export const setCoresListUseConfigModal = (value: boolean) => {
  if (typeof localStorage === 'undefined') return
  localStorage.setItem(CORES_LIST_USE_CONFIG_MODAL_KEY, value ? 'true' : 'false')
}

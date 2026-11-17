import { test } from 'node:test'
import assert from 'node:assert/strict'
import { NODE_DETAIL_TABS, validNodeDetailTab } from './node-detail-tabs'

test('node detail links accept only known tabs and fall back to overview', () => {
  for (const tab of NODE_DETAIL_TABS) assert.equal(validNodeDetailTab(tab), tab)
  for (const bad of [null, '', 'other', 'OVERview', '../../nodes', 'registry/x']) {
    assert.equal(validNodeDetailTab(bad), 'overview')
  }
})

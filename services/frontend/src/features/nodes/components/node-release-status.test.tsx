import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { NodeReleaseStatus, type NodeReleaseOperation } from './node-release-status'

const operation: NodeReleaseOperation = {
  operation_id: 'synthetic', generation: 1, action: 'update', image: null, previous_image: null,
  status: 'idle', phase: 'idle', progress: 0, error_code: null,
}

test('initial Node operation has no invented progress or success', () => {
  const notFetched = renderToStaticMarkup(createElement(NodeReleaseStatus, {}))
  assert.match(notFetched, /не загружено/)
  assert.doesNotMatch(notFetched, /Операции пока не выполнялись/)
  const idle = renderToStaticMarkup(createElement(NodeReleaseStatus, { operation }))
  assert.match(idle, /Операции пока не выполнялись/)
  assert.doesNotMatch(idle, /0%|<progress|Agent сообщил об успехе/)
})

test('queued update is not success and rollback is distinct from failure', () => {
  const queued = renderToStaticMarkup(createElement(NodeReleaseStatus, { operation: {
    ...operation, status: 'queued', phase: 'download', progress: 10,
  } }))
  assert.match(queued, /В очереди Agent/)
  assert.match(queued, /Этап: download/)
  assert.doesNotMatch(queued, /об успехе/)
  const rolledBack = renderToStaticMarkup(createElement(NodeReleaseStatus, { operation: {
    ...operation, status: 'rolled_back', phase: 'rollback', progress: 100,
  } }))
  assert.match(rolledBack, /Agent сообщил об откате/)
  assert.doesNotMatch(rolledBack, /<progress/)
})

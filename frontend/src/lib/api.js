export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

async function request(path, options) {
  let response
  try {
    response = await fetch(`${API_BASE_URL}${path}`, options)
  } catch {
    throw new Error('Could not reach the PurposeSeal backend. Is it running?')
  }

  if (!response.ok) {
    let message = `Request failed with status ${response.status}`
    try {
      const body = await response.json()
      if (body?.message) message = body.message
    } catch {
      // Response body wasn't JSON (or had none) -- keep the generic message.
    }
    throw new Error(message)
  }

  return response.json()
}

export async function fetchHealth() {
  return request('/health')
}

export async function listGrants() {
  return request('/grants')
}

export async function getGrant(grantId) {
  return request(`/grants/${grantId}`)
}

export async function listAssets(params = {}) {
  const query = new URLSearchParams(params).toString()
  return request(`/assets${query ? `?${query}` : ''}`)
}

export async function getAsset(assetId) {
  return request(`/assets/${assetId}`)
}

export async function getLineage(assetId) {
  return request(`/assets/${assetId}/lineage`)
}

export async function runScenario(scenarioKey) {
  return request(`/demo/scenarios/${scenarioKey}`, { method: 'POST' })
}

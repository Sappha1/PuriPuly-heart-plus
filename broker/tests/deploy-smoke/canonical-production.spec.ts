import { describe, expect, it } from 'vitest';

import { BROKER_SERVICE_NAME } from '../../src/contract';
import { MANAGED_TRIAL_ALLOWED_MODELS } from '../../src/trial-policy';
import {
  TRIAL_STATUS_SIGNATURE_HEADER,
  TRIAL_STATUS_TIMESTAMP_HEADER,
} from '../../src/trial-handshake';
import {
  createDeviceKeyPair,
  signCanonicalIssueRequest,
  signCanonicalStatusRequest,
  signCanonicalVerifyRequest,
} from '../test-support/ed25519';

const CANONICAL_WORKER_NAME = 'puripuly-heart-broker';
const ISSUE_REASON = 'llm_start';
const ISSUE_BUDGET_USD = 0.07;
const MANAGED_ALLOWLIST_MODELS = [...MANAGED_TRIAL_ALLOWED_MODELS] as const;
const ISSUE_MODEL = MANAGED_ALLOWLIST_MODELS[0];
const POSITIVE_ROUTING_PROBE_MODELS = MANAGED_ALLOWLIST_MODELS.filter(
  (model) => model !== ISSUE_MODEL,
);
const BOOTSTRAP_PLACEHOLDER = '__BOOTSTRAP_REQUIRED__';
const OPENROUTER_API_BASE_URL = new URL('https://openrouter.ai');
const smokeBaseUrl = process.env.BROKER_DEPLOY_SMOKE_BASE_URL?.trim();
const smokeDisallowedModel = normalizeDisallowedModel(
  process.env.BROKER_DEPLOY_SMOKE_DISALLOWED_MODEL,
  MANAGED_ALLOWLIST_MODELS,
  process.env.CI === 'true',
);
const MANAGED_OPENROUTER_USER_ID_PATTERN = /^ph-or-user-v\d+_[A-Za-z0-9_-]+$/u;
type JsonRequestOptions = {
  method: string;
  url: URL;
  body?: unknown;
  headers?: HeadersInit;
};

const describeDeploySmoke =
  smokeBaseUrl || process.env.CI === 'true' ? describe : describe.skip;

describe('broker deploy smoke helpers', () => {
  it('reads issued child-key metadata from the OpenRouter current-key payload', () => {
    expect(
      readOpenRouterCurrentKeyMetadata({
        data: {
          limit: ISSUE_BUDGET_USD,
          expires_at: '2026-07-08T06:00:00.000Z',
        },
      }),
    ).toEqual({
      limit: ISSUE_BUDGET_USD,
      expiresAt: '2026-07-08T06:00:00.000Z',
    });
  });

  it('recognizes model-routing failures as guardrail enforcement for a disallowed model probe', () => {
    expect(
      isDisallowedModelGuardrailFailure(503, {
        error: {
          code: 503,
          message: 'No allowed model/provider is available for this request.',
        },
      }),
    ).toBe(true);
    expect(
      isDisallowedModelGuardrailFailure(401, {
        error: {
          code: 401,
          message: 'Invalid credentials',
        },
      }),
    ).toBe(false);
  });

  it('accepts successful OpenRouter chat completion response shapes for managed model probes', () => {
    expect(() =>
      assertSuccessfulChatCompletionResponse(
        {
          status: 200,
          body: {
            id: 'chatcmpl-123',
            choices: [
              {
                message: {
                  role: 'assistant',
                  content: 'routed',
                },
              },
            ],
          },
        },
        'qwen/qwen3.5-flash-02-23',
      ),
    ).not.toThrow();
  });

  it('requires a distinct disallowed model probe when smoke runs in CI', () => {
    expect(normalizeDisallowedModel(undefined, MANAGED_ALLOWLIST_MODELS, false)).toBeUndefined();
    expect(
      normalizeDisallowedModel('openai/gpt-4o-mini', MANAGED_ALLOWLIST_MODELS, true),
    ).toBe('openai/gpt-4o-mini');
    expect(() =>
      normalizeDisallowedModel(ISSUE_MODEL, MANAGED_ALLOWLIST_MODELS, true),
    ).toThrow(/must differ from the managed allowlisted models/i);
    expect(() =>
      normalizeDisallowedModel(
        'qwen/qwen3.5-flash-02-23',
        MANAGED_ALLOWLIST_MODELS,
        true,
      ),
    ).toThrow(/must differ from the managed allowlisted models/i);
    expect(() =>
      normalizeDisallowedModel(
        'google/gemini-2.5-flash-lite',
        MANAGED_ALLOWLIST_MODELS,
        true,
      ),
    ).toThrow(/must differ from the managed allowlisted models/i);
    expect(() =>
      normalizeDisallowedModel(
        'deepseek/deepseek-v4-flash',
        MANAGED_ALLOWLIST_MODELS,
        true,
      ),
    ).toThrow(/must differ from the managed allowlisted models/i);
  });

  it('keeps the positive routing probes pinned to the managed secondary models', () => {
    expect(POSITIVE_ROUTING_PROBE_MODELS).toEqual([
      'qwen/qwen3.5-flash-02-23',
      'deepseek/deepseek-v4-flash',
      'google/gemini-2.5-flash-lite',
    ]);
    expect(MANAGED_ALLOWLIST_MODELS).toEqual(MANAGED_TRIAL_ALLOWED_MODELS);
  });
});

describeDeploySmoke('broker direct deploy smoke', () => {
  it('passes the canonical workers.dev trial flow', async () => {
    const baseUrl = normalizeSmokeBaseUrl(smokeBaseUrl);
    validateCanonicalWorkersDevTarget(baseUrl, CANONICAL_WORKER_NAME);

    const keyPair = await createDeviceKeyPair();
    const installationId = `deploy-smoke-${crypto.randomUUID().replace(/-/gu, '')}`.slice(
      0,
      64,
    );
    const appVersion = 'deploy-smoke-1.0.0';
    const hardwareHash = `deploy-smoke-hardware-${crypto.randomUUID()}`.slice(0, 96);

    const healthz = await requestJson({
      method: 'GET',
      url: new URL('/healthz', baseUrl),
    });
    expect(healthz.status).toBe(200);
    expect(healthz.body.ok).toBe(true);
    expect(healthz.body.service).toBe(BROKER_SERVICE_NAME);

    const foundation = await requestJson({
      method: 'GET',
      url: new URL('/v1/foundation', baseUrl),
    });
    expect(foundation.status).toBe(200);
    expect(foundation.body.service).toBe(BROKER_SERVICE_NAME);
    expect(foundation.body.trialProviderPolicy?.managedFreeTrial?.provider).toBe(
      'OpenRouter',
    );
    expect(foundation.body.trialProviderPolicy?.managedFreeTrial?.models).toEqual(
      expect.arrayContaining([...MANAGED_ALLOWLIST_MODELS]),
    );

    const challenge = await requestJson({
      method: 'POST',
      url: new URL('/v1/trial/challenge', baseUrl),
      body: {
        installation_id: installationId,
        device_public_key: keyPair.devicePublicKey,
        app_version: appVersion,
      },
    });
    expect(challenge.status).toBe(200);
    expect(typeof challenge.body.challenge).toBe('string');
    expect(typeof challenge.body.challenge_expires_at).toBe('string');
    expect(challenge.body.managed_state?.lifecycle).toBe('none');
    expect(challenge.body.fingerprint_salt?.current?.salt).not.toBe(
      BOOTSTRAP_PLACEHOLDER,
    );

    const verifySignedAt = timestampFromHeaders(challenge.headers);
    const verifyRequest = await signCanonicalVerifyRequest(keyPair.privateKey, {
      installation_id: installationId,
      device_public_key: keyPair.devicePublicKey,
      challenge: challenge.body.challenge,
      challenge_expires_at: challenge.body.challenge_expires_at,
      hardware_hash: hardwareHash,
      app_version: appVersion,
      signed_at: verifySignedAt,
    });
    const verify = await requestJson({
      method: 'POST',
      url: new URL('/v1/trial/challenge/verify', baseUrl),
      body: verifyRequest,
    });
    expect(verify.status).toBe(200);
    expect(typeof verify.body.release_token).toBe('string');
    expect(typeof verify.body.release_token_expires_at).toBe('string');
    expect(verify.body.managed_state?.lifecycle).toBe('pending_release');
    expect(verify.body.managed_state?.managed_availability).toBe(true);

    const statusTimestamp = timestampFromHeaders(verify.headers);
    const statusRequest = await signCanonicalStatusRequest(keyPair.privateKey, {
      installation_id: installationId,
      timestamp: statusTimestamp,
    });
    const statusUrl = new URL('/v1/trial/status', baseUrl);
    statusUrl.searchParams.set('installation_id', installationId);
    const status = await requestJson({
      method: 'GET',
      url: statusUrl,
      headers: {
        [TRIAL_STATUS_TIMESTAMP_HEADER]: statusRequest.timestamp,
        [TRIAL_STATUS_SIGNATURE_HEADER]: statusRequest.signature,
      },
    });
    expect(status.status).toBe(200);
    expect(status.body.managed_state?.lifecycle).toBe('pending_release');
    expect(status.body.current_entitlement?.provider).toBe('OpenRouter');

    const issueSignedAt = timestampFromHeaders(status.headers);
    const issueRequest = await signCanonicalIssueRequest(keyPair.privateKey, {
      installation_id: installationId,
      device_public_key: keyPair.devicePublicKey,
      release_token: verify.body.release_token,
      hardware_hash: hardwareHash,
      reason: ISSUE_REASON,
      budget_usd: ISSUE_BUDGET_USD,
      model: ISSUE_MODEL,
      signed_at: issueSignedAt,
    });
    const issue = await requestJson({
      method: 'POST',
      url: new URL('/v1/providers/openrouter/issue', baseUrl),
      body: issueRequest,
    });
    expect(issue.status).toBe(200);
    expect(issue.body.managed_state?.lifecycle).toBe('active');
    expect(issue.body.managed_state?.managed_availability).toBe(true);
    expect(issue.body.budget_usd).toBe(ISSUE_BUDGET_USD);
    expect(issue.body.model).toBe(ISSUE_MODEL);
    expect(typeof issue.body.openrouter_api_key).toBe('string');
    expect(issue.body.openrouter_api_key.length).toBeGreaterThan(0);
    expect(typeof issue.body.managed_credential_ref).toBe('string');
    expect(issue.body.managed_credential_ref.length).toBeGreaterThan(0);
    expect(typeof issue.body.expires_at).toBe('string');
    assertManagedOpenRouterUserId(issue.body.openrouter_user_id);

    const issuedKeyMetadata = readOpenRouterCurrentKeyMetadata(
      (
        await requestJson({
          method: 'GET',
          url: new URL('/api/v1/key', OPENROUTER_API_BASE_URL),
          headers: {
            authorization: `Bearer ${issue.body.openrouter_api_key}`,
          },
        })
      ).body,
    );
    expect(issuedKeyMetadata.limit).toBe(ISSUE_BUDGET_USD);
    expect(Date.parse(issuedKeyMetadata.expiresAt)).toBe(Date.parse(issue.body.expires_at));

    for (const managedModel of POSITIVE_ROUTING_PROBE_MODELS) {
      const managedModelProbe = await requestOpenRouterChatCompletion(
        issue.body.openrouter_api_key,
        managedModel,
        'Reply with the single word routed.',
      );

      assertSuccessfulChatCompletionResponse(managedModelProbe, managedModel);
    }

    const guardrailProbe = await requestOpenRouterChatCompletion(
      issue.body.openrouter_api_key,
      requireDisallowedModel(smokeDisallowedModel),
      'Reply with the single word blocked.',
    );
    expect(guardrailProbe.status).toBeGreaterThanOrEqual(400);
    expect(
      isDisallowedModelGuardrailFailure(guardrailProbe.status, guardrailProbe.body),
    ).toBe(true);
  }, 180_000);
});

function assertManagedOpenRouterUserId(value: unknown): asserts value is string {
  expect(typeof value).toBe('string');

  if (typeof value !== 'string') {
    throw new Error('issue success payload must include openrouter_user_id');
  }

  expect(value.trim().length).toBeGreaterThan(0);
  expect(value).toMatch(MANAGED_OPENROUTER_USER_ID_PATTERN);
}

function normalizeDisallowedModel(
  rawValue: string | undefined,
  managedAllowlistedModels: readonly string[],
  isCi: boolean,
): string | undefined {
  const normalized = rawValue?.trim();

  if (!normalized) {
    if (isCi) {
      throw new Error(
        'BROKER_DEPLOY_SMOKE_DISALLOWED_MODEL is required for CI smoke runs',
      );
    }

    return undefined;
  }

  if (managedAllowlistedModels.includes(normalized)) {
    throw new Error(
      'BROKER_DEPLOY_SMOKE_DISALLOWED_MODEL must differ from the managed allowlisted models',
    );
  }

  return normalized;
}

function requireDisallowedModel(model: string | undefined): string {
  if (!model) {
    throw new Error('BROKER_DEPLOY_SMOKE_DISALLOWED_MODEL is required for deploy smoke');
  }

  return model;
}

function normalizeSmokeBaseUrl(baseUrl: string | undefined): URL {
  if (!baseUrl) {
    throw new Error('BROKER_DEPLOY_SMOKE_BASE_URL is required for deploy smoke');
  }

  return new URL(baseUrl.endsWith('/') ? baseUrl : `${baseUrl}/`);
}

function validateCanonicalWorkersDevTarget(baseUrl: URL, canonicalWorkerName: string): void {
  if (baseUrl.protocol !== 'https:') {
    throw new Error('deploy smoke must target an https workers.dev URL');
  }

  if (!baseUrl.hostname.endsWith('.workers.dev')) {
    throw new Error('deploy smoke must target the canonical workers.dev hostname');
  }

  if (!baseUrl.hostname.startsWith(`${canonicalWorkerName}.`)) {
    throw new Error(
      `deploy smoke must target the canonical worker ${canonicalWorkerName}`,
    );
  }
}

function timestampFromHeaders(headers: Headers): string {
  const headerValue = headers.get('date');

  if (headerValue) {
    const parsed = Date.parse(headerValue);

    if (!Number.isNaN(parsed)) {
      return new Date(parsed).toISOString();
    }
  }

  return new Date().toISOString();
}

function readOpenRouterCurrentKeyMetadata(payload: unknown): {
  limit: number;
  expiresAt: string;
} {
  const data = readObjectField(payload, 'data', 'OpenRouter current-key response');
  const { limit } = data;
  const expiresAt = data.expires_at;

  if (typeof limit !== 'number' || !Number.isFinite(limit)) {
    throw new Error('OpenRouter current-key response must include a numeric data.limit');
  }

  if (typeof expiresAt !== 'string' || Number.isNaN(Date.parse(expiresAt))) {
    throw new Error(
      'OpenRouter current-key response must include a valid ISO timestamp in data.expires_at',
    );
  }

  return {
    limit,
    expiresAt,
  };
}

function assertSuccessfulChatCompletionResponse(
  response: { status: number; body: unknown },
  requestedModel: string,
): void {
  if (response.status !== 200) {
    throw new Error(
      `Expected successful chat completion for ${requestedModel}, got ${response.status}: ${stringifyForPatternMatch(response.body)}`,
    );
  }

  const payload = readRecord(
    response.body,
    `OpenRouter chat completion response for ${requestedModel}`,
  );
  const choices = readArrayField(
    payload,
    'choices',
    `OpenRouter chat completion response for ${requestedModel}`,
  );

  if (typeof payload.id !== 'string' || payload.id.length === 0) {
    throw new Error(
      `OpenRouter chat completion response for ${requestedModel} must include a non-empty id`,
    );
  }

  if (choices.length === 0) {
    throw new Error(
      `OpenRouter chat completion response for ${requestedModel} must include at least one choice`,
    );
  }

  const firstChoice = readRecord(
    choices[0],
    `OpenRouter first chat completion choice for ${requestedModel}`,
  );
  const message = readObjectField(
    firstChoice,
    'message',
    `OpenRouter first chat completion choice for ${requestedModel}`,
  );

  if (message.role !== 'assistant') {
    throw new Error(
      `OpenRouter chat completion response for ${requestedModel} must include an assistant message`,
    );
  }

  if (!hasNonEmptyChatCompletionContent(message.content)) {
    throw new Error(
      `OpenRouter chat completion response for ${requestedModel} must include non-empty assistant content`,
    );
  }
}

function isDisallowedModelGuardrailFailure(status: number, body: unknown): boolean {
  if (status < 400 || status === 401) {
    return false;
  }

  return /allowed model|disallowed model|model\/provider|model[^\n]*available|provider[^\n]*available|guardrail|route/iu.test(
    stringifyForPatternMatch(body),
  );
}

function readRecord(value: unknown, context: string): Record<string, unknown> {
  if (!isRecord(value)) {
    throw new Error(`${context} must be a JSON object`);
  }

  return value;
}

function readArrayField(
  value: unknown,
  fieldName: string,
  context: string,
): unknown[] {
  if (!isRecord(value) || !Array.isArray(value[fieldName])) {
    throw new Error(`${context} must include an array ${fieldName}`);
  }

  return value[fieldName] as unknown[];
}

function readObjectField(
  value: unknown,
  fieldName: string,
  context: string,
): Record<string, unknown> {
  if (!isRecord(value) || !isRecord(value[fieldName])) {
    throw new Error(`${context} must include an object ${fieldName}`);
  }

  return value[fieldName] as Record<string, unknown>;
}

function hasNonEmptyChatCompletionContent(content: unknown): boolean {
  if (typeof content === 'string') {
    return content.trim().length > 0;
  }

  return Array.isArray(content) && content.length > 0;
}

function stringifyForPatternMatch(value: unknown): string {
  if (typeof value === 'string') {
    return value;
  }

  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

async function requestJson({ method, url, body, headers = {} }: JsonRequestOptions) {
  const response = await fetch(url, {
    method,
    headers: {
      ...(body !== undefined ? { 'content-type': 'application/json' } : {}),
      ...headers,
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const rawText = await response.text();
  const safeText = redactIssueBody(rawText);

  if (!response.ok) {
    throw new Error(
      `${method} ${url.pathname} failed with ${response.status}: ${safeText}`,
    );
  }

  try {
    return {
      status: response.status,
      headers: response.headers,
      body: JSON.parse(rawText),
    };
  } catch {
    throw new Error(`${method} ${url.pathname} returned non-JSON: ${safeText}`);
  }
}

async function requestJsonAllowFailure({
  method,
  url,
  body,
  headers = {},
}: JsonRequestOptions) {
  const response = await fetch(url, {
    method,
    headers: {
      ...(body !== undefined ? { 'content-type': 'application/json' } : {}),
      ...headers,
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const rawText = await response.text();

  try {
    return {
      status: response.status,
      headers: response.headers,
      body: JSON.parse(rawText),
    };
  } catch {
    return {
      status: response.status,
      headers: response.headers,
      body: redactIssueBody(rawText),
    };
  }
}

async function requestOpenRouterChatCompletion(
  apiKey: string,
  model: string,
  prompt: string,
) {
  return requestJsonAllowFailure({
    method: 'POST',
    url: new URL('/api/v1/chat/completions', OPENROUTER_API_BASE_URL),
    headers: {
      authorization: `Bearer ${apiKey}`,
    },
    body: {
      model,
      messages: [
        {
          role: 'user',
          content: prompt,
        },
      ],
      max_tokens: 8,
    },
  });
}

function redactIssueBody(rawText: string): string {
  return rawText.replace(
    /"openrouter_api_key"\s*:\s*"[^"]+"/gu,
    '"openrouter_api_key":"[REDACTED]"',
  );
}

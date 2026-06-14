import { afterEach, describe, expect, it, vi } from 'vitest';

import { signCanonicalIssueRequest } from './test-support/ed25519';
import {
  createPendingReleaseSession,
  mockOpenRouterManagementApi,
} from './test-support/openrouter-issue';
import { createTestBrokerEnv } from './test-support/sqlite-d1';
import { postIssue } from './test-support/trial-api';
import {
  MANAGED_TRIAL_ENTITLEMENT_POLICY,
  MANAGED_TRIAL_LIVE_USAGE_POLICY,
} from '../src/contract';

describe('managed OpenRouter live usage source of truth', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it('keeps live usage and exhausted-budget detection sourced from OpenRouter instead of broker counters', () => {
    expect(MANAGED_TRIAL_LIVE_USAGE_POLICY).toEqual({
      managedAvailability: {
        field: 'managed_availability',
        reportedSeparatelyFromLifecycle: true,
      },
      sourceOfTruthAfterRelease: {
        provider: 'OpenRouter',
        signals: ['key-metadata', 'provider-failures'],
      },
      brokerTracksRemainingBudget: false,
    });
    expect(MANAGED_TRIAL_LIVE_USAGE_POLICY.managedAvailability).toBe(
      MANAGED_TRIAL_ENTITLEMENT_POLICY.managedAvailability,
    );
  });

  it('does not return live remaining-budget or usage counters from the issue response', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-04-08T06:00:00Z'));

    mockOpenRouterManagementApi();
    const env = createTestBrokerEnv();
    const release = await createPendingReleaseSession({
      env,
      installationId: 'install-issue-source-of-truth',
      appVersion: '1.2.3',
      hardwareHash: 'hardware-hash-issue-source-of-truth',
    });
    const requestBody = await signCanonicalIssueRequest(release.keyPair.privateKey, {
      installation_id: 'install-issue-source-of-truth',
      device_public_key: release.keyPair.devicePublicKey,
      release_token: release.releaseToken,
      hardware_hash: release.hardwareHash,
      reason: 'llm_start',
      budget_usd: 0.07,
      model: 'google/gemma-4-26b-a4b-it',
      signed_at: '2026-04-08T06:00:45.000Z',
    });

    const response = await postIssue(env, requestBody);
    expect(response.status).toBe(200);

    const payload = (await response.json()) as Record<string, unknown>;
    expect(payload).not.toHaveProperty('remaining_budget_usd');
    expect(payload).not.toHaveProperty('remaining_budget');
    expect(payload).not.toHaveProperty('usage_usd');
    expect(payload).not.toHaveProperty('usage');
    expect(payload).not.toHaveProperty('key_metadata');
  });
});

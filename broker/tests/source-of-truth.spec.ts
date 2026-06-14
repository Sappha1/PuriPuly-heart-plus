import { describe, expect, it } from 'vitest';

import app from '../src/index';
import {
  MANAGED_TRIAL_ENTITLEMENT_POLICY,
  MANAGED_TRIAL_LIVE_USAGE_POLICY,
} from '../src/contract';

describe('managed trial live usage source of truth', () => {
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

  it('publishes the same live-usage policy through the public foundation contract', async () => {
    const response = await app.request('http://broker.test/v1/foundation');
    expect(response.status).toBe(200);

    const payload = (await response.json()) as {
      managedTrialPolicy: {
        liveUsage: unknown;
      };
    };

    expect(payload.managedTrialPolicy.liveUsage).toEqual(
      MANAGED_TRIAL_LIVE_USAGE_POLICY,
    );
  });
});

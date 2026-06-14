import { describe, expect, it } from 'vitest';

import { derivePublicErrorRecovery } from '../src/broker-error';

describe('broker error recovery boundaries', () => {
  it('keeps retryable transport and runtime failures in retry mode without forcing restart', () => {
    expect(
      derivePublicErrorRecovery({
        code: 'rate_limited',
        class: 'retryable',
        subcode: 'installation_rate_limited',
        retry_after_ms: 900000,
      }),
    ).toEqual({
      behavior: 'retry',
      restartOnboarding: false,
      honorRetryAfterMs: true,
    });

    expect(
      derivePublicErrorRecovery({
        code: 'trial_unavailable',
        class: 'retryable',
        subcode: null,
        retry_after_ms: null,
      }),
    ).toEqual({
      behavior: 'retry',
      restartOnboarding: false,
      honorRetryAfterMs: false,
    });
  });

  it('requires onboarding restart for challenge/session invalidation boundaries', () => {
    expect(
      derivePublicErrorRecovery({
        code: 'challenge_expired',
        class: 'retryable',
        subcode: 'release_token_expired',
        retry_after_ms: 0,
      }),
    ).toEqual({
      behavior: 'restart',
      restartOnboarding: true,
      honorRetryAfterMs: false,
    });

    expect(
      derivePublicErrorRecovery({
        code: 'challenge_invalid',
        class: 'security_fail',
        subcode: 'signature_mismatch',
        retry_after_ms: null,
      }),
    ).toEqual({
      behavior: 'restart',
      restartOnboarding: true,
      honorRetryAfterMs: false,
    });
  });

  it('marks terminal request and eligibility failures as non-retryable', () => {
    expect(
      derivePublicErrorRecovery({
        code: 'invalid_request',
        class: 'terminal',
        subcode: null,
        retry_after_ms: null,
      }),
    ).toEqual({
      behavior: 'stop',
      restartOnboarding: false,
      honorRetryAfterMs: false,
    });

    expect(
      derivePublicErrorRecovery({
        code: 'trial_not_eligible',
        class: 'terminal',
        subcode: 'hardware_duplicate',
        retry_after_ms: null,
      }),
    ).toEqual({
      behavior: 'stop',
      restartOnboarding: false,
      honorRetryAfterMs: false,
    });
  });

  it('keeps internal managed-key failures in retry mode without forcing restart', () => {
    expect(
      derivePublicErrorRecovery({
        code: 'internal_error',
        class: 'retryable',
        subcode: 'managed_key_upstream_malformed',
        retry_after_ms: null,
      }),
    ).toEqual({
      behavior: 'retry',
      restartOnboarding: false,
      honorRetryAfterMs: false,
    });

    expect(
      derivePublicErrorRecovery({
        code: 'internal_error',
        class: 'retryable',
        subcode: 'managed_key_cleanup_failure',
        retry_after_ms: null,
      }),
    ).toEqual({
      behavior: 'retry',
      restartOnboarding: false,
      honorRetryAfterMs: false,
    });
  });
});

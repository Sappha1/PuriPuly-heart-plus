import { describe, expect, it } from 'vitest';

import { SERVICE_BOUNDARY } from '../src/contract';

describe('broker provider boundary', () => {
  it('keeps the broker scoped to an explicit non-proxy boundary', () => {
    expect(SERVICE_BOUNDARY).toEqual({
      role: 'trial-credential-broker',
      proxiesTranslationText: false,
      inferencePath: 'app-direct-to-openrouter',
    });
  });
});

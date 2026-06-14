import { describe, expect, it } from 'vitest';

import app from '../src/index';
import { SERVICE_BOUNDARY } from '../src/contract';

describe('broker non-proxy boundary', () => {
  it('does not expose a translation proxy endpoint', async () => {
    const response = await app.request('http://broker.test/v1/translate', {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
      },
      body: JSON.stringify({ text: 'hello world' }),
    });

    expect(response.status).toBe(404);
  });

  it('states that translation text never flows through the broker', async () => {
    const response = await app.request('http://broker.test/v1/foundation');
    expect(response.status).toBe(200);

    const payload = (await response.json()) as {
      serviceBoundary: {
        proxiesTranslationText: boolean;
      };
    };

    expect(SERVICE_BOUNDARY.proxiesTranslationText).toBe(false);
    expect(payload.serviceBoundary.proxiesTranslationText).toBe(false);
  });
});

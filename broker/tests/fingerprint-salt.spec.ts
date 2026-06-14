import { existsSync, readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';

const FIRST_MIGRATION = new URL(
  '../migrations/0000_define_broker_persistent_state.sql',
  import.meta.url,
);

describe('broker fingerprint salt persistence', () => {
  it('persists one shared server-managed salt in broker_config for cross-installation duplicate detection', async () => {
    const contract = await import('../src/contract');

    expect(contract).toHaveProperty('FINGERPRINT_SALT_POLICY', {
      configKey: 'fingerprint_salt',
      managedBy: 'broker',
      sharedAcrossClients: true,
      duplicateDetectionScope: 'cross-installation',
      storageModel: 'bounded-current-plus-previous',
      valueShape: {
        current: ['version', 'salt'],
        previous: ['version', 'salt', 'valid_until'],
        rotated_at: 'timestamp-or-null',
      },
      installationTracking: {
        challengeSaltVersionField: 'challenge_salt_version',
        hardwareHashSaltVersionField: 'hardware_hash_salt_version',
      },
      duplicateMatching: {
        hashField: 'hardware_hash',
        currentVersionOnly: true,
      },
      rotation: {
        newChallengesUse: 'current salt only',
        inFlightChallenges: 'accept previous salt version until challenge_expires_at',
        staleHardwareHash:
          'exclude non-current hardware_hash from duplicate matching until refreshed or cleared',
        migrationPath:
          'overwrite hardware_hash in place on next verify with current salt, otherwise clear on challenge reissue only for none or pending_release lifecycles',
      },
    });
  });

  it('tracks salt version on installations and seeds the fingerprint_salt config row in the migration', () => {
    expect(existsSync(FIRST_MIGRATION)).toBe(true);
    if (!existsSync(FIRST_MIGRATION)) {
      return;
    }

    const migration = readFileSync(FIRST_MIGRATION, 'utf8');

    expect(migration).toContain('hardware_hash_salt_version INTEGER');
    expect(migration).toContain('challenge_salt_version INTEGER');
    expect(migration).toContain('INSERT INTO broker_config (key, value)');
    expect(migration).toContain("'fingerprint_salt'");
  });
});

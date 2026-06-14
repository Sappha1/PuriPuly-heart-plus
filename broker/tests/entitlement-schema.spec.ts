import { readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';

import { BROKER_PERSISTENCE_MODEL } from '../src/contract';
import { readBrokerMigrationSql } from './test-support/migrations';
import { createTestBrokerEnv, insertEntitlement } from './test-support/sqlite-d1';

const OPENROUTER_ENTITLEMENT_COLUMNS = [
  'installation_id',
  'status',
  'budget_usd',
  'managed_credential_ref',
  'issued_at',
  'expires_at',
  'release_session_ref',
  'release_token_hash',
  'release_token_expires_at',
  'verified_hardware_hash',
  'verified_hardware_hash_salt_version',
  'discord_user_ref',
  'discord_issue_status',
  'discord_issue_reserved_at',
  'discord_issue_delivered_at',
];

const DISCORD_ENTITLEMENT_COLUMNS = [
  'discord_user_ref',
  'discord_issue_status',
  'discord_issue_reserved_at',
  'discord_issue_delivered_at',
];

describe('openrouter entitlement schema', () => {
  it('documents verified hardware snapshot columns in the persistence contract', () => {
    expect(BROKER_PERSISTENCE_MODEL.tables.openrouterEntitlements.columns).toEqual(
      OPENROUTER_ENTITLEMENT_COLUMNS,
    );
  });

  it('ships verified hardware snapshot columns in a forward entitlement migration', () => {
    expect(
      readBrokerMigrationSql('0000_define_broker_persistent_state.sql'),
    ).not.toContain('verified_hardware_hash TEXT');
    expect(
      readBrokerMigrationSql('0001_harden_installation_public_inputs.sql'),
    ).not.toContain('verified_hardware_hash TEXT');

    const migration = readBrokerMigrationSql(
      '0002_add_entitlement_verified_hardware_snapshot.sql',
    );

    expect(migration).toContain('ALTER TABLE openrouter_entitlements');
    expect(migration).toContain('verified_hardware_hash TEXT');
    expect(migration).toContain('verified_hardware_hash_salt_version INTEGER');
  });

  it('applies the verified hardware snapshot columns to migrated test databases', () => {
    const env = createTestBrokerEnv();
    const columns = env.__db
      .prepare("SELECT name FROM pragma_table_info('openrouter_entitlements') ORDER BY cid")
      .all() as Array<{ name: string }>;

    expect(columns.map((column) => column.name)).toEqual(OPENROUTER_ENTITLEMENT_COLUMNS);
  });

  it('selects Discord issue columns for full OpenRouter entitlement records', () => {
    for (const sourceFileName of ['trial-handshake.ts', 'openrouter-issue.ts']) {
      const source = readFileSync(
        new URL(`../src/${sourceFileName}`, import.meta.url),
        'utf8',
      );
      const fullRecordSelect = source.match(
        /SELECT installation_id, status, budget_usd, managed_credential_ref, issued_at,[\s\S]*?FROM openrouter_entitlements/u,
      )?.[0];

      expect(fullRecordSelect, `${sourceFileName} full entitlement SELECT`).toBeDefined();
      for (const column of DISCORD_ENTITLEMENT_COLUMNS) {
        expect(fullRecordSelect).toContain(column);
      }
    }
  });

  it('seeds the exact managed OpenRouter and Discord secret bindings in the sqlite D1 test env', () => {
    const env = createTestBrokerEnv() as Record<string, unknown>;

    expect({
      OPENROUTER_MANAGED_API_KEY: env.OPENROUTER_MANAGED_API_KEY,
      OPENROUTER_MANAGEMENT_API_KEY: env.OPENROUTER_MANAGEMENT_API_KEY,
      OPENROUTER_MANAGED_GUARDRAIL_ID: env.OPENROUTER_MANAGED_GUARDRAIL_ID,
      OPENROUTER_MANAGED_USER_HMAC_SECRET: env.OPENROUTER_MANAGED_USER_HMAC_SECRET,
      DISCORD_CLIENT_ID: env.DISCORD_CLIENT_ID,
      DISCORD_CLIENT_SECRET: env.DISCORD_CLIENT_SECRET,
      DISCORD_REDIRECT_URI_ALLOWLIST: env.DISCORD_REDIRECT_URI_ALLOWLIST,
      DISCORD_USER_REF_SECRET: env.DISCORD_USER_REF_SECRET,
    }).toEqual({
      OPENROUTER_MANAGED_API_KEY: 'test-managed-api-key',
      OPENROUTER_MANAGEMENT_API_KEY: 'test-management-api-key',
      OPENROUTER_MANAGED_GUARDRAIL_ID: 'test-managed-guardrail-id',
      OPENROUTER_MANAGED_USER_HMAC_SECRET: 'test-managed-user-hmac-secret',
      DISCORD_CLIENT_ID: 'test-discord-client-id',
      DISCORD_CLIENT_SECRET: 'test-discord-client-secret',
      DISCORD_REDIRECT_URI_ALLOWLIST:
        'http://127.0.0.1:62187/discord/callback,http://127.0.0.1:62188/discord/callback,http://127.0.0.1:62189/discord/callback',
      DISCORD_USER_REF_SECRET: 'test-discord-user-ref-secret',
    });
  });

  it('lets test helpers persist verified hardware snapshots on entitlement rows', () => {
    const env = createTestBrokerEnv();
    env.__db
      .prepare(
        `INSERT INTO installations (installation_id, device_public_key, app_version)
         VALUES (?, ?, ?)`,
      )
      .run('install-snapshot', 'device-public-key-snapshot', '1.0.0');

    insertEntitlement(env, {
      installation_id: 'install-snapshot',
      status: 'active',
      budget_usd: 0.05,
      managed_credential_ref: 'managed-credential-snapshot',
      verified_hardware_hash: 'verified-hardware-hash',
      verified_hardware_hash_salt_version: 7,
    });

    const row = env.__db
      .prepare(
        `SELECT verified_hardware_hash, verified_hardware_hash_salt_version
           FROM openrouter_entitlements
          WHERE installation_id = ?`,
      )
      .get('install-snapshot') as {
      verified_hardware_hash: string | null;
      verified_hardware_hash_salt_version: number | null;
    };

    expect(row).toEqual({
      verified_hardware_hash: 'verified-hardware-hash',
      verified_hardware_hash_salt_version: 7,
    });
  });
});

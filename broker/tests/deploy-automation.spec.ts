import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { fileURLToPath } from 'node:url';

import { afterEach, describe, expect, it } from 'vitest';

import { applyBrokerMigrations } from './test-support/migrations';

const renderWranglerConfigScript = new URL(
  '../scripts/render-production-wrangler-config.mjs',
  import.meta.url,
);
const renderFingerprintBootstrapScript = new URL(
  '../scripts/render-fingerprint-bootstrap-sql.mjs',
  import.meta.url,
);
const checkedInWranglerConfig = new URL('../wrangler.jsonc', import.meta.url);
const deployWorkflow = new URL(
  '../../.github/workflows/deploy-broker-direct.yml',
  import.meta.url,
);
const abuseControlsWorkflow = new URL(
  '../../.github/workflows/maintenance-broker-abuse-controls.yml',
  import.meta.url,
);
const deploySmokeSpec = new URL(
  './deploy-smoke/canonical-production.spec.ts',
  import.meta.url,
);
const brokerReadme = new URL('../README.md', import.meta.url);
const rolloutChecklist = new URL(
  '../../docs/plans/2026-04-09-cloudflare-staging-broker-rollout-checklist.md',
  import.meta.url,
);

const tempDirs: string[] = [];

afterEach(() => {
  for (const tempDir of tempDirs.splice(0)) {
    rmSync(tempDir, { force: true, recursive: true });
  }
});

describe('broker direct deploy automation', () => {
  it('renders a deploy-time wrangler config with the production database_id while preserving the canonical worker name', () => {
    const tempDir = createTempDir();
    const outputPath = join(tempDir, 'wrangler.production.jsonc');

    runNodeScript(renderWranglerConfigScript, [
      '--source',
      fileURLToPath(checkedInWranglerConfig),
      '--out',
      outputPath,
      '--database-id',
      'production-d1-database-id',
    ]);

    const renderedConfig = readFileSync(outputPath, 'utf8');
    expect(renderedConfig).toContain('"name": "puripuly-heart-broker"');
    expect(renderedConfig).toContain('"database_id": "production-d1-database-id"');
    expect(renderedConfig).not.toContain('REQUIRED_AT_DEPLOY_TIME');
  });

  it('fails config rendering if the checked-in worker name stops being canonical', () => {
    const tempDir = createTempDir();
    const sourcePath = join(tempDir, 'wrangler.noncanonical.jsonc');
    const outputPath = join(tempDir, 'wrangler.production.jsonc');

    writeFileSync(
      sourcePath,
      readFileSync(checkedInWranglerConfig, 'utf8').replace(
        '"name": "puripuly-heart-broker"',
        '"name": "puripuly-heart-broker-preview"',
      ),
      'utf8',
    );

    expect(() =>
      runNodeScript(renderWranglerConfigScript, [
        '--source',
        sourcePath,
        '--out',
        outputPath,
        '--database-id',
        'production-d1-database-id',
      ]),
    ).toThrow(/canonical worker name/i);
  });

  it('renders guarded fingerprint bootstrap SQL that replaces only the placeholder salt', () => {
    const tempDir = createTempDir();
    const outputPath = join(tempDir, 'fingerprint-bootstrap.sql');
    const bootstrapSalt = 'deploy-bootstrap-salt-01';

    runNodeScript(renderFingerprintBootstrapScript, [
      '--out',
      outputPath,
      '--salt',
      bootstrapSalt,
    ]);

    const renderedSql = readFileSync(outputPath, 'utf8');
    const db = new DatabaseSync(':memory:');

    try {
      expect(renderedSql).not.toContain('__BOOTSTRAP_REQUIRED__');
      expect(renderedSql).toContain(bootstrapSalt);
      expect(renderedSql).not.toContain('CREATE TEMP TABLE');
      expect(renderedSql).toContain("json_extract(value, '$.current.salt') = '__BOOTSTRAP' || '_REQUIRED__'");

      applyBrokerMigrations(db);
      db.exec(renderedSql);

      const row = db
        .prepare('SELECT value FROM broker_config WHERE key = ?')
        .get('fingerprint_salt') as { value: string };

      expect(JSON.parse(row.value)).toEqual({
        current: {
          version: 1,
          salt: bootstrapSalt,
        },
        previous: null,
        rotated_at: null,
      });
    } finally {
      db.close();
    }
  });

  it('leaves the fingerprint salt unchanged when the placeholder has already been replaced', () => {
    const tempDir = createTempDir();
    const outputPath = join(tempDir, 'fingerprint-bootstrap.sql');

    runNodeScript(renderFingerprintBootstrapScript, [
      '--out',
      outputPath,
      '--salt',
      'deploy-bootstrap-salt-02',
    ]);

    const renderedSql = readFileSync(outputPath, 'utf8');
    const db = new DatabaseSync(':memory:');

    try {
      applyBrokerMigrations(db);
      db.prepare('UPDATE broker_config SET value = ? WHERE key = ?').run(
        JSON.stringify({
          current: {
            version: 1,
            salt: 'already-bootstrapped',
          },
          previous: null,
          rotated_at: null,
        }),
        'fingerprint_salt',
      );

      db.exec(renderedSql);

      const row = db
        .prepare('SELECT value FROM broker_config WHERE key = ?')
        .get('fingerprint_salt') as { value: string };

      expect(JSON.parse(row.value)).toEqual({
        current: {
          version: 1,
          salt: 'already-bootstrapped',
        },
        previous: null,
        rotated_at: null,
      });
    } finally {
      db.close();
    }
  });

  it('ships a manual direct-deploy workflow that renders config, applies remote D1 changes, syncs the transitional and child-key management secrets, deploys the canonical worker, and runs smoke', () => {
    const workflow = readFileSync(deployWorkflow, 'utf8');
    const smokeSpec = readFileSync(deploySmokeSpec, 'utf8');
    const readme = readFileSync(brokerReadme, 'utf8');
    const checklist = readFileSync(rolloutChecklist, 'utf8');
    const managedUserHmacBlankCheckIndex = workflow.indexOf(
      'OPENROUTER_MANAGED_USER_HMAC_SECRET_PRODUCTION is required and must not be blank.',
    );
    const discordWebhookBlankCheckIndex = workflow.indexOf(
      'DISCORD_OPERATIONS_WEBHOOK_URL_PRODUCTION is required and must not be blank.',
    );
    const remoteD1MigrationIndex = workflow.indexOf(
      'wrangler d1 migrations apply',
    );
    const managedUserHmacSyncIndex = workflow.indexOf(
      'wrangler secret put OPENROUTER_MANAGED_USER_HMAC_SECRET',
    );
    const discordImmediateWebhookSyncIndex = workflow.indexOf(
      'wrangler secret put DISCORD_IMMEDIATE_ALERT_WEBHOOK_URL',
    );
    const discordDailyWebhookSyncIndex = workflow.indexOf(
      'wrangler secret put DISCORD_DAILY_REPORT_WEBHOOK_URL',
    );

    expect(workflow).toContain('workflow_dispatch:');
    expect(workflow).not.toContain('\npush:');
    expect(workflow).toContain('confirm_production_deploy');
    expect(workflow).toContain('environment: production');
    expect(workflow).toContain('BROKER_D1_DATABASE_ID_PRODUCTION');
    expect(workflow).toContain('OPENROUTER_MANAGED_API_KEY_PRODUCTION');
    expect(workflow).toContain('OPENROUTER_MANAGEMENT_API_KEY_PRODUCTION');
    expect(workflow).toContain('OPENROUTER_MANAGED_GUARDRAIL_ID_PRODUCTION');
    expect(workflow).toContain('OPENROUTER_MANAGED_USER_HMAC_SECRET_PRODUCTION');
    expect(workflow).toContain('DISCORD_OPERATIONS_WEBHOOK_URL_PRODUCTION');
    expect(workflow).toContain('BROKER_DEPLOY_SMOKE_DISALLOWED_MODEL_PRODUCTION');
    expect(workflow).toContain('BROKER_CANONICAL_WORKERS_DEV_URL');
    expect(workflow).toContain(
      'BROKER_DEPLOY_SMOKE_DISALLOWED_MODEL_PRODUCTION is required',
    );
    expect(workflow).toContain('must differ from the managed allowlisted models.');
    expect(workflow).toContain('ref: refs/heads/dev');
    expect(workflow).toContain('render-production-wrangler-config.mjs');
    expect(workflow).toContain('render-fingerprint-bootstrap-sql.mjs');
    expect(workflow).toContain("working-directory: broker");
    expect(workflow).toContain("deploy_dir='.deploy-direct'");
    expect(workflow).toContain("config_path='wrangler.production.jsonc'");
    expect(workflow).toContain('fingerprint-bootstrap.sql');
    expect(workflow).toMatch(/wrangler types --config/u);
    expect(workflow).toContain('BROKER_CANONICAL_WORKERS_DEV_URL is required');
    expect(workflow).toContain('refs/heads/dev');
    expect(workflow).toContain("broker/src/trial-policy.ts");
    expect(workflow).toContain('MANAGED_TRIAL_ALLOWED_MODELS was not found');
    expect(workflow).toContain('https://openrouter.ai/api/v1/guardrails/');
    expect(workflow).toContain('PATCH "$guardrail_url"');
    expect(workflow).toContain('allowed_models');
    expect(workflow).toContain('allowed_providers');
    expect(workflow).toContain('ignored_providers');
    expect(workflow).toContain('enforce_zdr');
    expect(workflow).toContain('must be cleared (null or [])');
    expect(workflow).toContain('GET guardrail');
    expect(workflow).toMatch(
      /wrangler d1 migrations apply\s+puripuly-heart-broker\s+--remote\s+--config/u,
    );
    expect(workflow).toMatch(
      /wrangler d1 execute\s+puripuly-heart-broker\s+--remote\s+--config/u,
    );
    expect(workflow).toContain("json_extract(value, '$.current.salt')");
    expect(workflow).toMatch(
      /wrangler secret put OPENROUTER_MANAGED_API_KEY --config/u,
    );
    expect(workflow).toMatch(
      /wrangler secret put OPENROUTER_MANAGEMENT_API_KEY --config/u,
    );
    expect(workflow).toMatch(
      /wrangler secret put OPENROUTER_MANAGED_GUARDRAIL_ID --config/u,
    );
    expect(workflow).toMatch(
      /wrangler secret put OPENROUTER_MANAGED_USER_HMAC_SECRET --config/u,
    );
    expect(workflow).toMatch(
      /wrangler secret put DISCORD_IMMEDIATE_ALERT_WEBHOOK_URL --config/u,
    );
    expect(workflow).toMatch(
      /wrangler secret put DISCORD_DAILY_REPORT_WEBHOOK_URL --config/u,
    );
    expect(managedUserHmacBlankCheckIndex).toBeGreaterThanOrEqual(0);
    expect(discordWebhookBlankCheckIndex).toBeGreaterThanOrEqual(0);
    expect(remoteD1MigrationIndex).toBeGreaterThanOrEqual(0);
    expect(managedUserHmacBlankCheckIndex).toBeLessThan(remoteD1MigrationIndex);
    expect(managedUserHmacSyncIndex).toBeGreaterThanOrEqual(0);
    expect(managedUserHmacBlankCheckIndex).toBeLessThan(managedUserHmacSyncIndex);
    expect(discordImmediateWebhookSyncIndex).toBeGreaterThanOrEqual(0);
    expect(discordDailyWebhookSyncIndex).toBeGreaterThanOrEqual(0);
    expect(discordWebhookBlankCheckIndex).toBeLessThan(discordImmediateWebhookSyncIndex);
    expect(discordWebhookBlankCheckIndex).toBeLessThan(discordDailyWebhookSyncIndex);
    expect(workflow).toMatch(/wrangler deploy --config/u);
    expect(workflow).toContain(
      'broker/tests/deploy-smoke/canonical-production.spec.ts',
    );
    expect(workflow).toContain('BROKER_DEPLOY_SMOKE_DISALLOWED_MODEL');
    expect(workflow).toContain('curl --fail');
    expect(workflow).toContain('timeout-minutes: 10');
    expect(workflow).toContain('app / public traffic');
    expect(workflow).toContain('transitional runtime compatibility');
    expect(workflow).toContain('managed child-key creation and cleanup');
    expect(workflow).toContain('assign the canonical production guardrail');
    expect(workflow).toContain('positive Qwen/DeepSeek/Gemini routing');
    expect(smokeSpec).toContain("process.env.CI === 'true'");
    expect(smokeSpec).toContain('/api/v1/key');
    expect(smokeSpec).toContain('/api/v1/chat/completions');
    expect(smokeSpec).toContain('BROKER_DEPLOY_SMOKE_DISALLOWED_MODEL');
    expect(smokeSpec).toContain('reads issued child-key metadata');
    expect(smokeSpec).toContain('recognizes model-routing failures as guardrail enforcement');
    expect(smokeSpec).toContain('assertSuccessfulChatCompletionResponse');
    expect(smokeSpec).toContain('assertManagedOpenRouterUserId');
    expect(smokeSpec).toContain('issue.body.openrouter_user_id');
    expect(smokeSpec).toContain('MANAGED_OPENROUTER_USER_ID_PATTERN');
    expect(smokeSpec).toContain('ph-or-user-v');
    expect(smokeSpec).toContain('MANAGED_TRIAL_ALLOWED_MODELS');
    expect(smokeSpec).toContain('qwen/qwen3.5-flash-02-23');
    expect(smokeSpec).toContain('deepseek/deepseek-v4-flash');
    expect(smokeSpec).toContain('google/gemini-2.5-flash-lite');
    expect(smokeSpec).toContain('MANAGED_TRIAL_ALLOWED_MODELS');
    expect(smokeSpec).toContain('must differ from the managed allowlisted models');
    expect(readme).toContain('per-installation OpenRouter child key');
    expect(readme).toContain('not the shared worker secret');
    expect(readme).toContain('BROKER_DEPLOY_SMOKE_DISALLOWED_MODEL_PRODUCTION');
    expect(readme).toContain('OPENROUTER_MANAGED_API_KEY_PRODUCTION` remains transitional');
    expect(readme).toContain('reconciles the production OpenRouter guardrail');
    expect(readme).toContain('OPENROUTER_MANAGED_USER_HMAC_SECRET_PRODUCTION');
    expect(readme).toContain('OPENROUTER_MANAGED_USER_HMAC_SECRET');
    expect(readme).toContain('DISCORD_OPERATIONS_WEBHOOK_URL_PRODUCTION');
    expect(readme).toContain('DISCORD_IMMEDIATE_ALERT_WEBHOOK_URL');
    expect(readme).toContain('DISCORD_DAILY_REPORT_WEBHOOK_URL');
    expect(readme).toContain('daily Discord heartbeat');
    expect(readme).toContain('three-month expiry');
    expect(readme).not.toContain('six-month expiry');
    expect(readme).toContain('optional `openrouter_user_id`');
    expect(readme).toContain('qwen/qwen3.5-flash-02-23');
    expect(readme).toContain('deepseek/deepseek-v4-flash');
    expect(readme).toContain('google/gemini-2.5-flash-lite');
    expect(checklist).toContain('OPENROUTER_MANAGEMENT_API_KEY_PRODUCTION');
    expect(checklist).toContain('OPENROUTER_MANAGED_GUARDRAIL_ID_PRODUCTION');
    expect(checklist).toContain('OPENROUTER_MANAGED_USER_HMAC_SECRET_PRODUCTION');
    expect(checklist).toContain('DISCORD_OPERATIONS_WEBHOOK_URL_PRODUCTION');
    expect(checklist).toContain('daily Discord heartbeat');
    expect(checklist).toContain('BROKER_DEPLOY_SMOKE_DISALLOWED_MODEL_PRODUCTION');
    expect(checklist).toContain('transitional compatibility only');
    expect(checklist).toContain('guardrail reconcile');
    expect(checklist).toContain('positive routing for');
  });

  it('ships a manual production workflow that updates only the broker daily auth cap runtime config', () => {
    const workflow = readFileSync(abuseControlsWorkflow, 'utf8');

    expect(workflow).toContain('workflow_dispatch:');
    expect(workflow).not.toContain('\npush:');
    expect(workflow).toContain('environment: production');
    expect(workflow).toContain('max_count');
    expect(workflow).toContain('default: "1000"');
    expect(workflow).toContain('confirm_update');
    expect(workflow).toContain('update broker daily auth cap');
    expect(workflow).toContain('CLOUDFLARE_API_TOKEN');
    expect(workflow).toContain('CLOUDFLARE_ACCOUNT_ID');
    expect(workflow).toContain('BROKER_D1_DATABASE_ID_PRODUCTION');
    expect(workflow).toContain('render-production-wrangler-config.mjs');
    expect(workflow).toContain('wrangler.production.jsonc');
    expect(workflow).toContain('wrangler d1 execute');
    expect(workflow).toContain('puripuly-heart-broker --remote --config');
    expect(workflow).toContain("json_set(value, '$.newActiveEntitlementsPerDay.maxCount'");
    expect(workflow).toContain("json_extract(value, '$.newActiveEntitlementsPerDay.maxCount')");
    expect(workflow).toContain('Daily auth cap verification failed');
    expect(workflow).not.toContain('wrangler deploy');
    expect(workflow).not.toContain('wrangler d1 migrations apply');
    expect(workflow).not.toContain('wrangler secret put');
  });
});

function createTempDir(): string {
  const tempDir = mkdtempSync(join(tmpdir(), 'broker-direct-deploy-'));
  tempDirs.push(tempDir);
  return tempDir;
}

function runNodeScript(scriptUrl: URL, args: string[]): string {
  return execFileSync(process.execPath, [fileURLToPath(scriptUrl), ...args], {
    encoding: 'utf8',
  });
}

import { getBrokerAbuseControlsConfig } from './abuse-controls';
import type { TalkTogetherPassStatusResponse } from './managed-state';
import { resolveEffectiveEntitlementLifecycle } from './managed-state';
import {
  readManagedChildKeyEffectiveLimit,
  updateManagedChildKeyLimit,
} from './openrouter-management';
import type {
  BrokerAbuseControlsConfigValue,
  OpenRouterEntitlementRecord,
  ReferralCodeRecord,
  ReferralRewardRecord,
} from './persistence';
import { MANAGED_TRIAL_BUDGET_POLICY } from './trial-policy';

export const REFERRAL_ID_LENGTH = 6;
export const REFERRAL_ID_ALPHABET = '23456789ABCDEFGHJKMNPQRSTUVWXYZ';
export const TALK_TOGETHER_PASS_INVITE_LIMIT = 5;
export const TALK_TOGETHER_PASS_BONUS_TRANSLATIONS_PER_FRIEND = 200;

const REFERRAL_ID_PATTERN = new RegExp(
  `^[${REFERRAL_ID_ALPHABET}]{${REFERRAL_ID_LENGTH}}$`,
  'u',
);
const REFERRAL_RANDOM_REJECTION_THRESHOLD =
  Math.floor(256 / REFERRAL_ID_ALPHABET.length) * REFERRAL_ID_ALPHABET.length;
const REFERRAL_ID_MAX_RANDOM_DRAWS = 64;
const OWNED_DISCORD_USER_REF_PATTERN = /^ph-discord-user-v\d+_[A-Za-z0-9_-]{32,128}$/u;
const DEFAULT_REFERRAL_ID_COLLISION_ATTEMPTS = 12;
const USD_CENTS = 100;
const REFERRER_REFERRAL_REWARD_CENTS = 2;
const REFERRER_APPLYING_LEASE_MS = 5 * 60 * 1000;
const REFERRER_REWARD_DRAIN_ATTEMPTS = 6;
const DEFAULT_STALE_RESERVED_RECONCILE_MS = 15 * 60 * 1000;
const DEFAULT_STALE_APPLYING_RECONCILE_MS = REFERRER_APPLYING_LEASE_MS;
const REFERRAL_REWARD_LOG_EVENT = 'referral_reward_outcome';

const ISSUE_REFERRAL_SKIP_REASONS = [
  'unknown_referral_id',
  'disabled_referral_id',
  'self_referral',
  'duplicate_hardware',
  'referred_already_rewarded',
  'referrer_cap_reached',
  'referrer_not_eligible',
  'referred_not_first_successful',
  'pre_existing_managed_user',
  'reservation_conflict',
  'referral_attempt_rate_limited',
  'unknown_referral_id_rate_limited',
  'referral_velocity_limited',
  'referrer_velocity_limited',
] as const;

const ISSUE_REFERRAL_FAILURE_REASONS = [
  'issue_delivery_failed',
  'referrer_patch_failed',
  'stale_reserved_reconciled',
] as const;

const REFERRAL_DISABLE_REASONS = [
  'abuse',
  'compromised',
  'operator_request',
  'policy_violation',
] as const;
const REFERRAL_DISABLE_ACTOR_PATTERN = /^[A-Za-z0-9._:-]{1,64}$/u;

export type ReferralIdRandomBytes = (byteLength: number) => Uint8Array;
export type ReferralIdGenerator = () => string;

export type ReferrerRewardLimitUpdateResult =
  | {
      outcome: 'not_applicable';
      reason: 'no_referrer_reward_rows' | 'no_pending_referrer_rewards';
    }
  | {
      outcome: 'applying';
      reason: 'active_lease';
    }
  | {
      outcome: 'skipped';
      reason: 'referrer_managed_key_missing';
      skippedRows: number;
    }
  | {
      outcome: 'failed';
      reason: 'referrer_patch_failed';
      failedRows: number;
    }
  | {
      outcome: 'credited';
      creditedRows: number;
      targetLimitUsd: number;
    };

export type IssueReferralSkipReason =
  | 'unknown_referral_id'
  | 'disabled_referral_id'
  | 'self_referral'
  | 'duplicate_hardware'
  | 'referred_already_rewarded'
  | 'referrer_cap_reached'
  | 'referrer_not_eligible'
  | 'referred_not_first_successful'
  | 'pre_existing_managed_user'
  | 'reservation_conflict'
  | 'referral_attempt_rate_limited'
  | 'unknown_referral_id_rate_limited'
  | 'referral_velocity_limited'
  | 'referrer_velocity_limited';

export type IssueReferralFailureReason =
  | 'issue_delivery_failed'
  | 'referrer_patch_failed'
  | 'stale_reserved_reconciled';

export type ReferralDisableReason = (typeof REFERRAL_DISABLE_REASONS)[number];

export type DisableReferralIdResult =
  | { ok: true; status: 'disabled' | 'already_disabled' }
  | {
      ok: false;
      reason:
        | 'invalid_referral_id'
        | 'invalid_disable_reason'
        | 'invalid_disabled_by'
        | 'not_found';
    };

export interface ReferralRewardRetentionResult {
  skippedDeleted: number;
  failedDeleted: number;
}

export interface StaleReferralRewardReconciliationResult {
  staleReservedCredited: number;
  staleReservedFailed: number;
  staleApplyingRequeued: number;
}

export type IssueReferralReservationResult =
  | {
      outcome: 'not_applicable';
      reason: 'no_referral_input' | 'malformed_referral_input';
    }
  | {
      outcome: 'reserved';
      referralId: string;
    }
  | {
      outcome: 'skipped';
      reason: IssueReferralSkipReason;
    };

export type OwnedReferralIdEnsureFailureReason =
  | 'not_eligible'
  | 'unsafe_discord_user_ref'
  | 'disabled'
  | 'collision_exhausted';

export type OwnedReferralIdEnsureResult =
  | {
      ok: true;
      referralCode: ReferralCodeRecord;
      created: boolean;
    }
  | {
      ok: false;
      reason: OwnedReferralIdEnsureFailureReason;
    };

interface ActiveDiscordManagedReferralOwner extends OpenRouterEntitlementRecord {
  installation_id: string;
  discord_user_ref: string;
  managed_credential_ref: string;
  expires_at: string;
  discord_issue_status: 'active';
  discord_issue_delivered_at: string;
}

export function normalizeReferralId(value: unknown): string | null {
  if (typeof value !== 'string') {
    return null;
  }

  const normalized = value.trim().toUpperCase();
  if (!normalized || !REFERRAL_ID_PATTERN.test(normalized)) {
    return null;
  }

  return normalized;
}

export async function reserveIssueReferralReward(
  db: D1Database,
  input: {
    referralId: string | null;
    referredDiscordUserRef: string;
    referredInstallationId: string;
    referredHardwareHash: string;
    referredHardwareHashSaltVersion: number;
    clientIp?: string | null;
    nowIso: string;
  },
): Promise<IssueReferralReservationResult> {
  if (input.referralId === null || input.referralId.trim().length === 0) {
    return { outcome: 'not_applicable', reason: 'no_referral_input' };
  }

  const referralId = normalizeReferralId(input.referralId);
  if (!referralId) {
    return { outcome: 'not_applicable', reason: 'malformed_referral_input' };
  }

  const controls = await getBrokerAbuseControlsConfig(db);
  const attemptIpHash = await hashReferralAttemptIp(input.clientIp ?? null);
  const existingCode = await getReferralCodeByReferralId(db, referralId);

  if (
    await isValidShapedReferralAttemptRateLimited(db, {
      referredInstallationId: input.referredInstallationId,
      attemptIpHash,
      nowIso: input.nowIso,
      controls,
    })
  ) {
    const referrerFields = existingCode
      ? referralRewardReferrerFields(existingCode)
      : { referrerDiscordUserRef: null, referrerInstallationId: null };
    await insertSkippedIssueReferralReward(db, {
      referralId,
      ...referrerFields,
      referredDiscordUserRef: input.referredDiscordUserRef,
      referredInstallationId: input.referredInstallationId,
      referredHardwareHash: input.referredHardwareHash,
      referredHardwareHashSaltVersion: input.referredHardwareHashSaltVersion,
      skipReason: 'referral_attempt_rate_limited',
      attemptIpHash,
      nowIso: input.nowIso,
    });
    return { outcome: 'skipped', reason: 'referral_attempt_rate_limited' };
  }

  if (!existingCode) {
    const reason: IssueReferralSkipReason = (await isUnknownReferralAttemptRateLimited(
      db,
      {
        referredInstallationId: input.referredInstallationId,
        attemptIpHash,
        nowIso: input.nowIso,
        controls,
      },
    ))
      ? 'unknown_referral_id_rate_limited'
      : 'unknown_referral_id';
    await insertSkippedIssueReferralReward(db, {
      referralId,
      referrerDiscordUserRef: null,
      referrerInstallationId: null,
      referredDiscordUserRef: input.referredDiscordUserRef,
      referredInstallationId: input.referredInstallationId,
      referredHardwareHash: input.referredHardwareHash,
      referredHardwareHashSaltVersion: input.referredHardwareHashSaltVersion,
      skipReason: reason,
      attemptIpHash,
      nowIso: input.nowIso,
    });
    return { outcome: 'skipped', reason };
  }

  if (existingCode.status !== 'active') {
    const referrerFields = referralRewardReferrerFields(existingCode);
    await insertSkippedIssueReferralReward(db, {
      referralId,
      ...referrerFields,
      referredDiscordUserRef: input.referredDiscordUserRef,
      referredInstallationId: input.referredInstallationId,
      referredHardwareHash: input.referredHardwareHash,
      referredHardwareHashSaltVersion: input.referredHardwareHashSaltVersion,
      skipReason: 'disabled_referral_id',
      attemptIpHash,
      nowIso: input.nowIso,
    });
    return { outcome: 'skipped', reason: 'disabled_referral_id' };
  }

  if (
    await isReferralIdVelocityLimited(db, {
      referralId,
      nowIso: input.nowIso,
      controls,
    })
  ) {
    const referrerFields = referralRewardReferrerFields(existingCode);
    await insertSkippedIssueReferralReward(db, {
      referralId,
      ...referrerFields,
      referredDiscordUserRef: input.referredDiscordUserRef,
      referredInstallationId: input.referredInstallationId,
      referredHardwareHash: input.referredHardwareHash,
      referredHardwareHashSaltVersion: input.referredHardwareHashSaltVersion,
      skipReason: 'referral_velocity_limited',
      attemptIpHash,
      nowIso: input.nowIso,
    });
    return { outcome: 'skipped', reason: 'referral_velocity_limited' };
  }

  const reserved = await insertReservedIssueReferralReward(db, {
    referralId,
    referredDiscordUserRef: input.referredDiscordUserRef,
    referredInstallationId: input.referredInstallationId,
    referredHardwareHash: input.referredHardwareHash,
    referredHardwareHashSaltVersion: input.referredHardwareHashSaltVersion,
    attemptIpHash,
    controls,
    nowIso: input.nowIso,
  });
  if (reserved) {
    logReferralRewardOutcome({
      outcome: 'reserved',
      referralId,
      referredInstallationId: input.referredInstallationId,
      referrerDiscordUserRef: existingCode.owner_discord_user_ref,
    });
    return { outcome: 'reserved', referralId };
  }

  const skip = await resolveIssueReferralSkip(db, {
    referralId,
    referredDiscordUserRef: input.referredDiscordUserRef,
    referredInstallationId: input.referredInstallationId,
    referredHardwareHash: input.referredHardwareHash,
    referredHardwareHashSaltVersion: input.referredHardwareHashSaltVersion,
    controls,
    nowIso: input.nowIso,
  });
  await insertSkippedIssueReferralReward(db, {
    referralId,
    referrerDiscordUserRef: skip.referrerDiscordUserRef,
    referrerInstallationId: skip.referrerInstallationId,
    referredDiscordUserRef: input.referredDiscordUserRef,
    referredInstallationId: input.referredInstallationId,
    referredHardwareHash: input.referredHardwareHash,
    referredHardwareHashSaltVersion: input.referredHardwareHashSaltVersion,
    skipReason: skip.reason,
    attemptIpHash,
    nowIso: input.nowIso,
  });

  return { outcome: 'skipped', reason: skip.reason };
}

export async function markReservedIssueReferralFailed(
  db: D1Database,
  input: {
    referralId: string;
    referredDiscordUserRef: string;
    referredInstallationId: string;
    failureReason: 'issue_delivery_failed';
    nowIso: string;
  },
): Promise<void> {
  assertIssueReferralFailureReason(input.failureReason);
  await db
    .prepare(
      `UPDATE referral_rewards
          SET referred_bonus_status = 'failed',
              referrer_bonus_status = 'failed',
              failure_reason = ?,
              updated_at = ?
        WHERE referral_id = ?
          AND referred_discord_user_ref = ?
          AND referred_installation_id = ?
          AND referred_bonus_status = 'reserved'`,
    )
    .bind(
      input.failureReason,
      input.nowIso,
      input.referralId,
      input.referredDiscordUserRef,
      input.referredInstallationId,
    )
    .run();
  logReferralRewardOutcome({
    outcome: 'failed',
    referralId: input.referralId,
    referredInstallationId: input.referredInstallationId,
    reason: input.failureReason,
  });
}

export async function markReservedIssueReferralCredited(
  db: D1Database,
  input: {
    referralId: string;
    referredDiscordUserRef: string;
    referredInstallationId: string;
    referredManagedCredentialRef: string;
    nowIso: string;
  },
): Promise<boolean> {
  const result = await db
    .prepare(
      `UPDATE referral_rewards
          SET referred_bonus_status = 'credited',
              referred_managed_credential_ref = ?,
              updated_at = ?,
              credited_at = ?
        WHERE referral_id = ?
          AND referred_discord_user_ref = ?
          AND referred_installation_id = ?
          AND referred_bonus_status = 'reserved'`,
    )
    .bind(
      input.referredManagedCredentialRef,
      input.nowIso,
      input.nowIso,
      input.referralId,
      input.referredDiscordUserRef,
      input.referredInstallationId,
    )
    .run();

  const credited = Number(result.meta.changes ?? 0) === 1;
  if (credited) {
    logReferralRewardOutcome({
      outcome: 'credited',
      referralId: input.referralId,
      referredInstallationId: input.referredInstallationId,
    });
  }
  return credited;
}

export async function applyCreditedIssueReferrerRewardLimitUpdate(
  db: D1Database,
  input: {
    referralId: string;
    referredDiscordUserRef: string;
    referredInstallationId: string;
    managementApiKey: string;
    nowIso: string;
    fetchImpl?: typeof fetch;
  },
): Promise<ReferrerRewardLimitUpdateResult> {
  const referrerDiscordUserRef = await getCreditedIssueReferralReferrer(db, {
    referralId: input.referralId,
    referredDiscordUserRef: input.referredDiscordUserRef,
    referredInstallationId: input.referredInstallationId,
  });
  if (!referrerDiscordUserRef) {
    return { outcome: 'not_applicable', reason: 'no_referrer_reward_rows' };
  }

  return applyReferrerRewardLimitUpdates(db, {
    referrerDiscordUserRef,
    managementApiKey: input.managementApiKey,
    nowIso: input.nowIso,
    fetchImpl: input.fetchImpl,
  });
}

export async function applyReferrerRewardLimitUpdates(
  db: D1Database,
  input: {
    referrerDiscordUserRef: string;
    managementApiKey: string;
    nowIso: string;
    fetchImpl?: typeof fetch;
  },
): Promise<ReferrerRewardLimitUpdateResult> {
  let lastCreditedResult: Extract<
    ReferrerRewardLimitUpdateResult,
    { outcome: 'credited' }
  > | null = null;

  for (let attempt = 0; attempt < REFERRER_REWARD_DRAIN_ATTEMPTS; attempt += 1) {
    const result = await applyReferrerRewardLimitUpdateAttempt(db, input);
    if (result.outcome !== 'credited') {
      return result;
    }

    lastCreditedResult = result;
    if (
      !(await hasPendingReferrerRewardRows(db, {
        referrerDiscordUserRef: input.referrerDiscordUserRef,
      }))
    ) {
      return result;
    }
  }

  return (
    lastCreditedResult ?? {
      outcome: 'not_applicable',
      reason: 'no_pending_referrer_rewards',
    }
  );
}

async function applyReferrerRewardLimitUpdateAttempt(
  db: D1Database,
  input: {
    referrerDiscordUserRef: string;
    managementApiKey: string;
    nowIso: string;
    fetchImpl?: typeof fetch;
  },
): Promise<ReferrerRewardLimitUpdateResult> {
  const now = new Date(input.nowIso);
  if (Number.isNaN(now.getTime())) {
    throw new Error('nowIso must be a valid ISO timestamp');
  }

  const activeEntitlement = await getActiveReferrerRewardEntitlement(db, {
    referrerDiscordUserRef: input.referrerDiscordUserRef,
    nowIso: input.nowIso,
  });
  if (!activeEntitlement?.managed_credential_ref) {
    const skippedRows = await markReferrerRewardRowsSkipped(db, {
      referrerDiscordUserRef: input.referrerDiscordUserRef,
      nowIso: input.nowIso,
      skipReason: 'referrer_managed_key_missing',
    });
    if (skippedRows === 0) {
      return { outcome: 'not_applicable', reason: 'no_pending_referrer_rewards' };
    }

    return {
      outcome: 'skipped',
      reason: 'referrer_managed_key_missing',
      skippedRows,
    };
  }

  const managedCredentialRef = activeEntitlement.managed_credential_ref;
  const leaseCutoffIso = new Date(
    now.getTime() - REFERRER_APPLYING_LEASE_MS,
  ).toISOString();
  const claimedRows = await claimReferrerRewardApplicationLease(db, {
    referrerDiscordUserRef: input.referrerDiscordUserRef,
    managedCredentialRef,
    nowIso: input.nowIso,
    leaseCutoffIso,
  });
  if (claimedRows === 0) {
    if (
      await hasActiveReferrerRewardApplicationLease(db, {
        referrerDiscordUserRef: input.referrerDiscordUserRef,
        managedCredentialRef,
        leaseCutoffIso,
      })
    ) {
      logReferralRewardOutcome({
        outcome: 'applying',
        referrerDiscordUserRef: input.referrerDiscordUserRef,
        referrerManagedCredentialRef: managedCredentialRef,
        reason: 'active_lease',
      });
      return { outcome: 'applying', reason: 'active_lease' };
    }

    return { outcome: 'not_applicable', reason: 'no_pending_referrer_rewards' };
  }

  try {
    const reflectedRewardCount = await countReferrerRewardsForTargetLimit(db, {
      referrerDiscordUserRef: input.referrerDiscordUserRef,
      managedCredentialRef,
    });
    const ledgerTargetLimitUsd = referrerRewardTargetLimitUsd(reflectedRewardCount);
    const providerLimitUsd = await readManagedChildKeyEffectiveLimit({
      managementApiKey: input.managementApiKey,
      keyHash: managedCredentialRef,
      fetchImpl: input.fetchImpl,
    });
    const targetLimitUsd = maxUsd(
      ledgerTargetLimitUsd,
      providerLimitUsd,
      activeEntitlement.budget_usd,
    );
    let verifiedLimitUsd = providerLimitUsd;

    if (currencyCents(providerLimitUsd) < currencyCents(targetLimitUsd)) {
      verifiedLimitUsd = await updateManagedChildKeyLimit({
        managementApiKey: input.managementApiKey,
        keyHash: managedCredentialRef,
        limitUsd: targetLimitUsd,
        fetchImpl: input.fetchImpl,
      });
    }

    const consistentLimitUsd = maxUsd(targetLimitUsd, verifiedLimitUsd);
    const budgetUpdated = await updateReferrerEntitlementBudget(db, {
      referrerDiscordUserRef: input.referrerDiscordUserRef,
      installationId: activeEntitlement.installation_id,
      managedCredentialRef,
      budgetUsd: consistentLimitUsd,
    });
    if (!budgetUpdated) {
      throw new Error('referrer entitlement budget update failed');
    }

    const creditedRows = await markReferrerRewardRowsCredited(db, {
      referrerDiscordUserRef: input.referrerDiscordUserRef,
      managedCredentialRef,
      nowIso: input.nowIso,
    });
    if (creditedRows > 0) {
      logReferralRewardOutcome({
        outcome: 'credited',
        referrerDiscordUserRef: input.referrerDiscordUserRef,
        referrerManagedCredentialRef: managedCredentialRef,
        affectedRows: creditedRows,
      });
    }
    return {
      outcome: 'credited',
      creditedRows,
      targetLimitUsd: consistentLimitUsd,
    };
  } catch {
    const failedRows = await markReferrerRewardRowsFailed(db, {
      referrerDiscordUserRef: input.referrerDiscordUserRef,
      managedCredentialRef,
      nowIso: input.nowIso,
      failureReason: 'referrer_patch_failed',
    });
    return {
      outcome: 'failed',
      reason: 'referrer_patch_failed',
      failedRows,
    };
  }
}

export async function recordSkippedIssueReferralReward(
  db: D1Database,
  input: {
    referralId: string | null;
    referredDiscordUserRef: string;
    referredInstallationId: string;
    referredHardwareHash: string;
    referredHardwareHashSaltVersion: number;
    skipReason: IssueReferralSkipReason;
    clientIp?: string | null;
    nowIso: string;
  },
): Promise<IssueReferralReservationResult> {
  if (input.referralId === null || input.referralId.trim().length === 0) {
    return { outcome: 'not_applicable', reason: 'no_referral_input' };
  }

  const referralId = normalizeReferralId(input.referralId);
  if (!referralId) {
    return { outcome: 'not_applicable', reason: 'malformed_referral_input' };
  }

  const skip = await resolveForcedIssueReferralSkip(db, {
    referralId,
    fallbackReason: input.skipReason,
  });
  const attemptIpHash = await hashReferralAttemptIp(input.clientIp ?? null);
  await insertSkippedIssueReferralReward(db, {
    referralId,
    referrerDiscordUserRef: skip.referrerDiscordUserRef,
    referrerInstallationId: skip.referrerInstallationId,
    referredDiscordUserRef: input.referredDiscordUserRef,
    referredInstallationId: input.referredInstallationId,
    referredHardwareHash: input.referredHardwareHash,
    referredHardwareHashSaltVersion: input.referredHardwareHashSaltVersion,
    skipReason: skip.reason,
    attemptIpHash,
    nowIso: input.nowIso,
  });

  return { outcome: 'skipped', reason: skip.reason };
}

export async function reconcileStaleReferralRewards(
  db: D1Database,
  input: {
    nowIso: string;
    staleReservedAfterMinutes?: number;
    staleApplyingAfterMinutes?: number;
  },
): Promise<StaleReferralRewardReconciliationResult> {
  const now = new Date(input.nowIso);
  if (Number.isNaN(now.getTime())) {
    throw new Error('nowIso must be a valid ISO timestamp');
  }

  const reservedCutoffIso = new Date(
    now.getTime() -
      (input.staleReservedAfterMinutes === undefined
        ? DEFAULT_STALE_RESERVED_RECONCILE_MS
        : input.staleReservedAfterMinutes * 60_000),
  ).toISOString();
  const applyingCutoffIso = new Date(
    now.getTime() -
      (input.staleApplyingAfterMinutes === undefined
        ? DEFAULT_STALE_APPLYING_RECONCILE_MS
        : input.staleApplyingAfterMinutes * 60_000),
  ).toISOString();

  let staleReservedCredited = 0;
  let staleReservedFailed = 0;
  const staleReservedRows = await listStaleReservedReferralRewards(db, reservedCutoffIso);
  for (const row of staleReservedRows) {
    const deliveredEntitlement = await getDeliveredReferredEntitlement(db, row);
    if (deliveredEntitlement?.managed_credential_ref) {
      const credited = await reconcileStaleReservedReferralToCredited(db, {
        rewardId: row.id,
        referralId: row.referral_id,
        referredInstallationId: row.referred_installation_id,
        referrerDiscordUserRef: row.referrer_discord_user_ref,
        managedCredentialRef: deliveredEntitlement.managed_credential_ref,
        nowIso: input.nowIso,
      });
      staleReservedCredited += credited;
      continue;
    }

    const failed = await reconcileStaleReservedReferralToFailed(db, {
      rewardId: row.id,
      referralId: row.referral_id,
      referredInstallationId: row.referred_installation_id,
      referrerDiscordUserRef: row.referrer_discord_user_ref,
      nowIso: input.nowIso,
    });
    staleReservedFailed += failed;
  }

  const staleApplyingRequeued = await requeueStaleApplyingReferralRewards(db, {
    cutoffIso: applyingCutoffIso,
    nowIso: input.nowIso,
  });

  return {
    staleReservedCredited,
    staleReservedFailed,
    staleApplyingRequeued,
  };
}

export async function applyReferralRewardRetention(
  db: D1Database,
  now: Date,
): Promise<ReferralRewardRetentionResult> {
  if (Number.isNaN(now.getTime())) {
    throw new Error('now must be a valid Date');
  }

  const controls = await getBrokerAbuseControlsConfig(db);
  const skippedDeleted = await deleteTerminalReferralRewardsOlderThan(db, {
    referredBonusStatus: 'skipped',
    cutoffIso: new Date(
      now.getTime() - controls.retention.referralSkippedDays * 24 * 60 * 60_000,
    ).toISOString(),
  });
  const failedDeleted = await deleteTerminalReferralRewardsOlderThan(db, {
    referredBonusStatus: 'failed',
    cutoffIso: new Date(
      now.getTime() - controls.retention.referralFailedDays * 24 * 60 * 60_000,
    ).toISOString(),
  });

  return {
    skippedDeleted,
    failedDeleted,
  };
}

export async function disableReferralId(
  db: D1Database,
  input: {
    referralId: string;
    reason: unknown;
    disabledBy: unknown;
    nowIso: string;
  },
): Promise<DisableReferralIdResult> {
  const referralId = normalizeReferralId(input.referralId);
  if (!referralId) {
    return { ok: false, reason: 'invalid_referral_id' };
  }

  const disableReason = normalizeReferralDisableReason(input.reason);
  if (!disableReason) {
    return { ok: false, reason: 'invalid_disable_reason' };
  }

  const disabledBy = normalizeReferralDisableActor(input.disabledBy);
  if (!disabledBy) {
    return { ok: false, reason: 'invalid_disabled_by' };
  }

  const existing = await getReferralCodeByReferralId(db, referralId);
  if (!existing) {
    return { ok: false, reason: 'not_found' };
  }

  if (existing.status === 'disabled') {
    return { ok: true, status: 'already_disabled' };
  }

  await db
    .prepare(
      `UPDATE referral_codes
          SET status = 'disabled',
              disabled_reason = ?,
              disabled_by = ?,
              disabled_at = ?,
              updated_at = ?
        WHERE referral_id = ?
          AND status = 'active'`,
    )
    .bind(disableReason, disabledBy, input.nowIso, input.nowIso, referralId)
    .run();
  await appendReferralRuntimeAudit(db, {
    eventKind: 'referral_id_disabled',
    reason: disableReason,
    payload: {
      referral_id: referralId,
      disabled_by: disabledBy,
      previous_status: existing.status,
    },
    createdAt: input.nowIso,
  });
  logReferralRewardOutcome({
    outcome: 'disabled',
    referralId,
    reason: disableReason,
  });

  return { ok: true, status: 'disabled' };
}

export function generateReferralId(
  randomBytes: ReferralIdRandomBytes = cryptoReferralRandomBytes,
): string {
  let referralId = '';
  let drawCount = 0;

  while (referralId.length < REFERRAL_ID_LENGTH) {
    drawCount += 1;
    if (drawCount > REFERRAL_ID_MAX_RANDOM_DRAWS) {
      throw new Error('unable to generate Referral ID from random source');
    }

    const bytes = randomBytes(REFERRAL_ID_LENGTH - referralId.length);
    if (bytes.length === 0) {
      throw new Error('Referral ID random source returned no bytes');
    }

    for (const byte of bytes) {
      if (byte >= REFERRAL_RANDOM_REJECTION_THRESHOLD) {
        continue;
      }

      referralId += REFERRAL_ID_ALPHABET[byte % REFERRAL_ID_ALPHABET.length];
      if (referralId.length === REFERRAL_ID_LENGTH) {
        break;
      }
    }
  }

  return referralId;
}

async function isValidShapedReferralAttemptRateLimited(
  db: D1Database,
  input: {
    referredInstallationId: string;
    attemptIpHash: string | null;
    nowIso: string;
    controls: BrokerAbuseControlsConfigValue;
  },
): Promise<boolean> {
  const config = input.controls.referralAttempts.validShaped;
  const windowStart = windowStartIso(input.nowIso, config.windowMinutes);
  const installationCount = await countReferralAttemptsByInstallation(db, {
    referredInstallationId: input.referredInstallationId,
    windowStartIso: windowStart,
  });
  if (installationCount >= config.maxPerInstallation) {
    return true;
  }

  if (!input.attemptIpHash) {
    return false;
  }

  const ipCount = await countReferralAttemptsByIpHash(db, {
    attemptIpHash: input.attemptIpHash,
    windowStartIso: windowStart,
  });
  return ipCount >= config.maxPerIp;
}

async function isUnknownReferralAttemptRateLimited(
  db: D1Database,
  input: {
    referredInstallationId: string;
    attemptIpHash: string | null;
    nowIso: string;
    controls: BrokerAbuseControlsConfigValue;
  },
): Promise<boolean> {
  const config = input.controls.referralAttempts.unknown;
  const windowStart = windowStartIso(input.nowIso, config.windowMinutes);
  const installationCount = await countUnknownReferralAttemptsByInstallation(db, {
    referredInstallationId: input.referredInstallationId,
    windowStartIso: windowStart,
  });
  if (installationCount >= config.maxPerInstallation) {
    return true;
  }

  if (!input.attemptIpHash) {
    return false;
  }

  const ipCount = await countUnknownReferralAttemptsByIpHash(db, {
    attemptIpHash: input.attemptIpHash,
    windowStartIso: windowStart,
  });
  return ipCount >= config.maxPerIp;
}

async function isReferralIdVelocityLimited(
  db: D1Database,
  input: {
    referralId: string;
    nowIso: string;
    controls: BrokerAbuseControlsConfigValue;
  },
): Promise<boolean> {
  const config = input.controls.referralAttempts.perReferralIdVelocity;
  const count = await countReferralAttemptsForReferralId(db, {
    referralId: input.referralId,
    windowStartIso: windowStartIso(input.nowIso, config.windowMinutes),
  });
  return count >= config.maxAttempts;
}

async function isReferrerRewardVelocityLimited(
  db: D1Database,
  input: {
    referrerDiscordUserRef: string;
    nowIso: string;
    controls: BrokerAbuseControlsConfigValue;
  },
): Promise<boolean> {
  const config = input.controls.referralAttempts.perReferrerRewardVelocity;
  const count = await countRecentCountedRewardsForReferrer(db, {
    referrerDiscordUserRef: input.referrerDiscordUserRef,
    windowStartIso: windowStartIso(input.nowIso, config.windowMinutes),
  });
  return count >= config.maxRewards;
}

async function countReferralAttemptsByInstallation(
  db: D1Database,
  input: { referredInstallationId: string; windowStartIso: string },
): Promise<number> {
  const row = await db
    .prepare(
      `SELECT COUNT(*) AS count
         FROM referral_rewards
        WHERE referred_installation_id = ?
          AND created_at >= ?`,
    )
    .bind(input.referredInstallationId, input.windowStartIso)
    .first<{ count: number }>();
  return Number(row?.count ?? 0);
}

async function countReferralAttemptsByIpHash(
  db: D1Database,
  input: { attemptIpHash: string; windowStartIso: string },
): Promise<number> {
  const row = await db
    .prepare(
      `SELECT COUNT(*) AS count
         FROM referral_rewards
        WHERE attempt_ip_hash = ?
          AND created_at >= ?`,
    )
    .bind(input.attemptIpHash, input.windowStartIso)
    .first<{ count: number }>();
  return Number(row?.count ?? 0);
}

async function countUnknownReferralAttemptsByInstallation(
  db: D1Database,
  input: { referredInstallationId: string; windowStartIso: string },
): Promise<number> {
  const row = await db
    .prepare(
      `SELECT COUNT(*) AS count
         FROM referral_rewards
        WHERE referred_installation_id = ?
          AND skip_reason IN ('unknown_referral_id', 'unknown_referral_id_rate_limited')
          AND created_at >= ?`,
    )
    .bind(input.referredInstallationId, input.windowStartIso)
    .first<{ count: number }>();
  return Number(row?.count ?? 0);
}

async function countUnknownReferralAttemptsByIpHash(
  db: D1Database,
  input: { attemptIpHash: string; windowStartIso: string },
): Promise<number> {
  const row = await db
    .prepare(
      `SELECT COUNT(*) AS count
         FROM referral_rewards
        WHERE attempt_ip_hash = ?
          AND skip_reason IN ('unknown_referral_id', 'unknown_referral_id_rate_limited')
          AND created_at >= ?`,
    )
    .bind(input.attemptIpHash, input.windowStartIso)
    .first<{ count: number }>();
  return Number(row?.count ?? 0);
}

async function countReferralAttemptsForReferralId(
  db: D1Database,
  input: { referralId: string; windowStartIso: string },
): Promise<number> {
  const row = await db
    .prepare(
      `SELECT COUNT(*) AS count
         FROM referral_rewards
        WHERE referral_id = ?
          AND created_at >= ?`,
    )
    .bind(input.referralId, input.windowStartIso)
    .first<{ count: number }>();
  return Number(row?.count ?? 0);
}

async function countRecentCountedRewardsForReferrer(
  db: D1Database,
  input: { referrerDiscordUserRef: string; windowStartIso: string },
): Promise<number> {
  const row = await db
    .prepare(
      `SELECT COUNT(*) AS count
         FROM referral_rewards
        WHERE referrer_discord_user_ref = ?
          AND referred_bonus_status IN ('reserved', 'credited')
          AND created_at >= ?`,
    )
    .bind(input.referrerDiscordUserRef, input.windowStartIso)
    .first<{ count: number }>();
  return Number(row?.count ?? 0);
}

function windowStartIso(nowIso: string, windowMinutes: number): string {
  const now = new Date(nowIso);
  if (Number.isNaN(now.getTime())) {
    throw new Error('nowIso must be a valid ISO timestamp');
  }

  return new Date(now.getTime() - windowMinutes * 60_000).toISOString();
}

async function hashReferralAttemptIp(clientIp: string | null): Promise<string | null> {
  const normalized = clientIp?.trim();
  if (!normalized) {
    return null;
  }

  const digest = await crypto.subtle.digest(
    'SHA-256',
    new TextEncoder().encode(`puripuly-heart:referral-attempt-ip:v1\n${normalized}`),
  );
  return Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, '0'),
  ).join('');
}

async function insertReservedIssueReferralReward(
  db: D1Database,
  input: {
    referralId: string;
    referredDiscordUserRef: string;
    referredInstallationId: string;
    referredHardwareHash: string;
    referredHardwareHashSaltVersion: number;
    attemptIpHash: string | null;
    controls: BrokerAbuseControlsConfigValue;
    nowIso: string;
  },
): Promise<boolean> {
  const referralVelocityWindowStartIso = windowStartIso(
    input.nowIso,
    input.controls.referralAttempts.perReferralIdVelocity.windowMinutes,
  );
  const referrerVelocityWindowStartIso = windowStartIso(
    input.nowIso,
    input.controls.referralAttempts.perReferrerRewardVelocity.windowMinutes,
  );
  const result = await db
    .prepare(
      `INSERT OR IGNORE INTO referral_rewards (
          referral_id,
          referrer_discord_user_ref,
          referrer_installation_id,
          referred_discord_user_ref,
          referred_installation_id,
          referred_hardware_hash,
          referred_hardware_hash_salt_version,
          referred_bonus_status,
          referrer_bonus_status,
          skip_reason,
          failure_reason,
          referred_managed_credential_ref,
          referrer_managed_credential_ref,
          attempt_ip_hash,
          created_at,
          updated_at
        )
        SELECT code.referral_id,
               code.owner_discord_user_ref,
               code.owner_installation_id,
               ?,
               ?,
               ?,
               ?,
               'reserved',
               'pending',
               NULL,
                NULL,
                NULL,
                NULL,
                ?,
                ?,
                ?
           FROM referral_codes code
         WHERE code.referral_id = ?
           AND code.status = 'active'
           AND code.owner_installation_id IS NOT NULL
           AND EXISTS (
             SELECT 1
               FROM discord_identities identity
              WHERE identity.discord_user_ref = code.owner_discord_user_ref
                AND identity.entitlement_installation_id = code.owner_installation_id
                AND identity.status = 'active'
           )
           AND code.owner_discord_user_ref <> ?
           AND NOT EXISTS (
             SELECT 1
               FROM openrouter_entitlements referrer_entitlement
              WHERE referrer_entitlement.discord_user_ref = code.owner_discord_user_ref
                AND referrer_entitlement.status = 'active'
                AND referrer_entitlement.discord_issue_status = 'active'
                AND referrer_entitlement.verified_hardware_hash = ?
                AND referrer_entitlement.verified_hardware_hash_salt_version = ?
           )
           AND (
             SELECT COUNT(*)
               FROM referral_rewards counted
              WHERE counted.referrer_discord_user_ref = code.owner_discord_user_ref
                AND counted.referred_bonus_status IN ('reserved', 'credited')
           ) < ?
           AND NOT EXISTS (
             SELECT 1
               FROM referral_rewards counted_referred
              WHERE counted_referred.referred_bonus_status IN ('reserved', 'credited')
                AND (
                 counted_referred.referred_discord_user_ref = ?
                 OR counted_referred.referred_installation_id = ?
               )
            )
            AND (
              SELECT COUNT(*)
                FROM referral_rewards referral_velocity
               WHERE referral_velocity.referral_id = code.referral_id
                 AND referral_velocity.created_at >= ?
            ) < ?
            AND (
              SELECT COUNT(*)
                FROM referral_rewards referrer_velocity
               WHERE referrer_velocity.referrer_discord_user_ref = code.owner_discord_user_ref
                 AND referrer_velocity.referred_bonus_status IN ('reserved', 'credited')
                 AND referrer_velocity.created_at >= ?
            ) < ?`,
    )
    .bind(
      input.referredDiscordUserRef,
      input.referredInstallationId,
      input.referredHardwareHash,
      input.referredHardwareHashSaltVersion,
      input.attemptIpHash,
      input.nowIso,
      input.nowIso,
      input.referralId,
      input.referredDiscordUserRef,
      input.referredHardwareHash,
      input.referredHardwareHashSaltVersion,
      TALK_TOGETHER_PASS_INVITE_LIMIT,
      input.referredDiscordUserRef,
      input.referredInstallationId,
      referralVelocityWindowStartIso,
      input.controls.referralAttempts.perReferralIdVelocity.maxAttempts,
      referrerVelocityWindowStartIso,
      input.controls.referralAttempts.perReferrerRewardVelocity.maxRewards,
    )
    .run();

  return Number(result.meta.changes ?? 0) === 1;
}

async function resolveIssueReferralSkip(
  db: D1Database,
  input: {
    referralId: string;
    referredDiscordUserRef: string;
    referredInstallationId: string;
    referredHardwareHash: string;
    referredHardwareHashSaltVersion: number;
    controls: BrokerAbuseControlsConfigValue;
    nowIso: string;
  },
): Promise<{
  reason: IssueReferralSkipReason;
  referrerDiscordUserRef: string | null;
  referrerInstallationId: string | null;
}> {
  const code = await getReferralCodeByReferralId(db, input.referralId);
  if (!code) {
    return {
      reason: 'unknown_referral_id',
      referrerDiscordUserRef: null,
      referrerInstallationId: null,
    };
  }

  const referrerFields = referralRewardReferrerFields(code);
  if (code.status !== 'active') {
    return { reason: 'disabled_referral_id', ...referrerFields };
  }

  if (code.owner_discord_user_ref === input.referredDiscordUserRef) {
    return { reason: 'self_referral', ...referrerFields };
  }

  if (!code.owner_installation_id) {
    return { reason: 'referrer_not_eligible', ...referrerFields };
  }

  if (
    !(await hasActiveReferralOwnerIdentity(db, {
      referrerDiscordUserRef: code.owner_discord_user_ref,
      referrerInstallationId: code.owner_installation_id,
    }))
  ) {
    return { reason: 'referrer_not_eligible', ...referrerFields };
  }

  if (await hasIssueReferralDuplicateHardware(db, input, code.owner_discord_user_ref)) {
    return { reason: 'duplicate_hardware', ...referrerFields };
  }

  if (await hasCountedIssueReferralForReferred(db, input)) {
    return { reason: 'referred_already_rewarded', ...referrerFields };
  }

  if (await hasReachedIssueReferralCap(db, code.owner_discord_user_ref)) {
    return { reason: 'referrer_cap_reached', ...referrerFields };
  }

  if (
    await isReferralIdVelocityLimited(db, {
      referralId: input.referralId,
      nowIso: input.nowIso,
      controls: input.controls,
    })
  ) {
    return { reason: 'referral_velocity_limited', ...referrerFields };
  }

  if (
    await isReferrerRewardVelocityLimited(db, {
      referrerDiscordUserRef: code.owner_discord_user_ref,
      nowIso: input.nowIso,
      controls: input.controls,
    })
  ) {
    return { reason: 'referrer_velocity_limited', ...referrerFields };
  }

  return { reason: 'reservation_conflict', ...referrerFields };
}

async function resolveForcedIssueReferralSkip(
  db: D1Database,
  input: {
    referralId: string;
    fallbackReason: IssueReferralSkipReason;
  },
): Promise<{
  reason: IssueReferralSkipReason;
  referrerDiscordUserRef: string | null;
  referrerInstallationId: string | null;
}> {
  const code = await getReferralCodeByReferralId(db, input.referralId);
  if (!code) {
    return {
      reason: 'unknown_referral_id',
      referrerDiscordUserRef: null,
      referrerInstallationId: null,
    };
  }

  const referrerFields = referralRewardReferrerFields(code);
  if (code.status !== 'active') {
    return { reason: 'disabled_referral_id', ...referrerFields };
  }

  return { reason: input.fallbackReason, ...referrerFields };
}

function referralRewardReferrerFields(code: ReferralCodeRecord): {
  referrerDiscordUserRef: string | null;
  referrerInstallationId: string | null;
} {
  if (!code.owner_installation_id) {
    return {
      referrerDiscordUserRef: null,
      referrerInstallationId: null,
    };
  }

  return {
    referrerDiscordUserRef: code.owner_discord_user_ref,
    referrerInstallationId: code.owner_installation_id,
  };
}

async function getReferralCodeByReferralId(
  db: D1Database,
  referralId: string,
): Promise<ReferralCodeRecord | null> {
  return db
    .prepare(
      `SELECT referral_id,
              owner_discord_user_ref,
              owner_installation_id,
              status,
              created_at,
              updated_at
         FROM referral_codes
        WHERE referral_id = ?`,
    )
    .bind(referralId)
    .first<ReferralCodeRecord>();
}

async function hasIssueReferralDuplicateHardware(
  db: D1Database,
  input: {
    referredHardwareHash: string;
    referredHardwareHashSaltVersion: number;
  },
  referrerDiscordUserRef: string,
): Promise<boolean> {
  const row = await db
    .prepare(
      `SELECT EXISTS(
          SELECT 1
            FROM openrouter_entitlements referrer_entitlement
           WHERE referrer_entitlement.discord_user_ref = ?
             AND referrer_entitlement.status = 'active'
             AND referrer_entitlement.discord_issue_status = 'active'
             AND referrer_entitlement.verified_hardware_hash = ?
             AND referrer_entitlement.verified_hardware_hash_salt_version = ?
        ) AS duplicate_found`,
    )
    .bind(
      referrerDiscordUserRef,
      input.referredHardwareHash,
      input.referredHardwareHashSaltVersion,
    )
    .first<{ duplicate_found: number }>();

  return Number(row?.duplicate_found ?? 0) === 1;
}

async function hasActiveReferralOwnerIdentity(
  db: D1Database,
  input: {
    referrerDiscordUserRef: string;
    referrerInstallationId: string;
  },
): Promise<boolean> {
  const row = await db
    .prepare(
      `SELECT EXISTS(
          SELECT 1
            FROM discord_identities identity
           WHERE identity.discord_user_ref = ?
             AND identity.entitlement_installation_id = ?
             AND identity.status = 'active'
        ) AS active_found`,
    )
    .bind(input.referrerDiscordUserRef, input.referrerInstallationId)
    .first<{ active_found: number }>();

  return Number(row?.active_found ?? 0) === 1;
}

async function hasCountedIssueReferralForReferred(
  db: D1Database,
  input: {
    referredDiscordUserRef: string;
    referredInstallationId: string;
  },
): Promise<boolean> {
  const row = await db
    .prepare(
      `SELECT EXISTS(
          SELECT 1
            FROM referral_rewards counted
           WHERE counted.referred_bonus_status IN ('reserved', 'credited')
             AND (
               counted.referred_discord_user_ref = ?
               OR counted.referred_installation_id = ?
             )
        ) AS counted_found`,
    )
    .bind(input.referredDiscordUserRef, input.referredInstallationId)
    .first<{ counted_found: number }>();

  return Number(row?.counted_found ?? 0) === 1;
}

export async function resolveTalkTogetherPassStatusForOwnedReferralCode(
  db: D1Database,
  referralCode: Pick<ReferralCodeRecord, 'referral_id' | 'owner_discord_user_ref'>,
): Promise<TalkTogetherPassStatusResponse> {
  const inviteCount = await countCountedIssueReferralRewardsForReferrer(
    db,
    referralCode.owner_discord_user_ref,
  );
  return {
    pass_id: referralCode.referral_id,
    invite_count: Math.min(inviteCount, TALK_TOGETHER_PASS_INVITE_LIMIT),
    invite_limit: TALK_TOGETHER_PASS_INVITE_LIMIT,
    bonus_translations_per_friend: TALK_TOGETHER_PASS_BONUS_TRANSLATIONS_PER_FRIEND,
  };
}

async function countCountedIssueReferralRewardsForReferrer(
  db: D1Database,
  referrerDiscordUserRef: string,
): Promise<number> {
  const row = await db
    .prepare(
      `SELECT COUNT(*) AS count
         FROM referral_rewards counted
        WHERE counted.referrer_discord_user_ref = ?
          AND counted.referred_bonus_status IN ('reserved', 'credited')`,
    )
    .bind(referrerDiscordUserRef)
    .first<{ count: number }>();

  return Math.max(0, Number(row?.count ?? 0));
}

async function hasReachedIssueReferralCap(
  db: D1Database,
  referrerDiscordUserRef: string,
): Promise<boolean> {
  return (
    (await countCountedIssueReferralRewardsForReferrer(db, referrerDiscordUserRef)) >=
    TALK_TOGETHER_PASS_INVITE_LIMIT
  );
}

async function insertSkippedIssueReferralReward(
  db: D1Database,
  input: {
    referralId: string;
    referrerDiscordUserRef: string | null;
    referrerInstallationId: string | null;
    referredDiscordUserRef: string;
    referredInstallationId: string;
    referredHardwareHash: string;
    referredHardwareHashSaltVersion: number;
    skipReason: IssueReferralSkipReason;
    attemptIpHash?: string | null;
    nowIso: string;
  },
): Promise<void> {
  assertIssueReferralSkipReason(input.skipReason);
  await db
    .prepare(
      `INSERT INTO referral_rewards (
          referral_id,
          referrer_discord_user_ref,
          referrer_installation_id,
          referred_discord_user_ref,
          referred_installation_id,
          referred_hardware_hash,
          referred_hardware_hash_salt_version,
          referred_bonus_status,
          referrer_bonus_status,
          skip_reason,
          failure_reason,
          referred_managed_credential_ref,
          referrer_managed_credential_ref,
          attempt_ip_hash,
          created_at,
          updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'skipped', 'skipped', ?, NULL, NULL, NULL, ?, ?, ?)`,
    )
    .bind(
      input.referralId,
      input.referrerDiscordUserRef,
      input.referrerInstallationId,
      input.referredDiscordUserRef,
      input.referredInstallationId,
      input.referredHardwareHash,
      input.referredHardwareHashSaltVersion,
      input.skipReason,
      input.attemptIpHash ?? null,
      input.nowIso,
      input.nowIso,
    )
    .run();
  logReferralRewardOutcome({
    outcome: 'skipped',
    referralId: input.referralId,
    referredInstallationId: input.referredInstallationId,
    referrerDiscordUserRef: input.referrerDiscordUserRef,
    reason: input.skipReason,
  });
}

async function getCreditedIssueReferralReferrer(
  db: D1Database,
  input: {
    referralId: string;
    referredDiscordUserRef: string;
    referredInstallationId: string;
  },
): Promise<string | null> {
  const row = await db
    .prepare(
      `SELECT referrer_discord_user_ref
         FROM referral_rewards
        WHERE referral_id = ?
          AND referred_discord_user_ref = ?
          AND referred_installation_id = ?
          AND referred_bonus_status = 'credited'
          AND referrer_bonus_status IN ('pending', 'applying', 'credited')`,
    )
    .bind(input.referralId, input.referredDiscordUserRef, input.referredInstallationId)
    .first<{ referrer_discord_user_ref: string | null }>();

  return row?.referrer_discord_user_ref ?? null;
}

async function getActiveReferrerRewardEntitlement(
  db: D1Database,
  input: {
    referrerDiscordUserRef: string;
    nowIso: string;
  },
): Promise<OpenRouterEntitlementRecord | null> {
  const row = await db
    .prepare(
      `SELECT installation_id,
              status,
              budget_usd,
              managed_credential_ref,
              issued_at,
              expires_at,
              release_session_ref,
              release_token_hash,
              release_token_expires_at,
              verified_hardware_hash,
              verified_hardware_hash_salt_version,
              discord_user_ref,
              discord_issue_status,
              discord_issue_reserved_at,
              discord_issue_delivered_at
         FROM openrouter_entitlements
        WHERE discord_user_ref = ?
          AND status = 'active'
          AND discord_issue_status = 'active'
          AND managed_credential_ref IS NOT NULL
          AND length(trim(managed_credential_ref)) > 0`,
    )
    .bind(input.referrerDiscordUserRef)
    .first<OpenRouterEntitlementRecord>();

  if (!row) {
    return null;
  }

  const now = new Date(input.nowIso);
  if (Number.isNaN(now.getTime())) {
    return null;
  }

  return resolveEffectiveEntitlementLifecycle(row, now) === 'active' ? row : null;
}

async function claimReferrerRewardApplicationLease(
  db: D1Database,
  input: {
    referrerDiscordUserRef: string;
    managedCredentialRef: string;
    nowIso: string;
    leaseCutoffIso: string;
  },
): Promise<number> {
  const result = await db
    .prepare(
      `UPDATE referral_rewards
          SET referrer_bonus_status = 'applying',
              referrer_managed_credential_ref = ?,
              failure_reason = NULL,
              updated_at = ?
        WHERE referrer_discord_user_ref = ?
          AND referred_bonus_status = 'credited'
          AND (
            referrer_bonus_status = 'pending'
            OR (
              referrer_bonus_status = 'applying'
              AND updated_at < ?
            )
          )
          AND (
            referrer_managed_credential_ref IS NULL
            OR referrer_managed_credential_ref = ?
          )
          AND NOT EXISTS (
            SELECT 1
              FROM referral_rewards active_lease
             WHERE active_lease.referrer_discord_user_ref = ?
               AND active_lease.referred_bonus_status = 'credited'
               AND active_lease.referrer_bonus_status = 'applying'
               AND active_lease.updated_at >= ?
               AND (
                 active_lease.referrer_managed_credential_ref IS NULL
                 OR active_lease.referrer_managed_credential_ref = ?
               )
          )`,
    )
    .bind(
      input.managedCredentialRef,
      input.nowIso,
      input.referrerDiscordUserRef,
      input.leaseCutoffIso,
      input.managedCredentialRef,
      input.referrerDiscordUserRef,
      input.leaseCutoffIso,
      input.managedCredentialRef,
    )
    .run();

  return Number(result.meta.changes ?? 0);
}

async function hasActiveReferrerRewardApplicationLease(
  db: D1Database,
  input: {
    referrerDiscordUserRef: string;
    managedCredentialRef: string;
    leaseCutoffIso: string;
  },
): Promise<boolean> {
  const row = await db
    .prepare(
      `SELECT EXISTS(
          SELECT 1
            FROM referral_rewards active_lease
           WHERE active_lease.referrer_discord_user_ref = ?
             AND active_lease.referred_bonus_status = 'credited'
             AND active_lease.referrer_bonus_status = 'applying'
             AND active_lease.updated_at >= ?
             AND (
               active_lease.referrer_managed_credential_ref IS NULL
               OR active_lease.referrer_managed_credential_ref = ?
             )
        ) AS active_found`,
    )
    .bind(input.referrerDiscordUserRef, input.leaseCutoffIso, input.managedCredentialRef)
    .first<{ active_found: number }>();

  return Number(row?.active_found ?? 0) === 1;
}

async function hasPendingReferrerRewardRows(
  db: D1Database,
  input: {
    referrerDiscordUserRef: string;
  },
): Promise<boolean> {
  const row = await db
    .prepare(
      `SELECT EXISTS(
          SELECT 1
            FROM referral_rewards pending_reward
           WHERE pending_reward.referrer_discord_user_ref = ?
             AND pending_reward.referred_bonus_status = 'credited'
             AND pending_reward.referrer_bonus_status = 'pending'
        ) AS pending_found`,
    )
    .bind(input.referrerDiscordUserRef)
    .first<{ pending_found: number }>();

  return Number(row?.pending_found ?? 0) === 1;
}

async function countReferrerRewardsForTargetLimit(
  db: D1Database,
  input: {
    referrerDiscordUserRef: string;
    managedCredentialRef: string;
  },
): Promise<number> {
  const row = await db
    .prepare(
      `SELECT COUNT(*) AS count
         FROM referral_rewards
        WHERE referrer_discord_user_ref = ?
          AND referred_bonus_status = 'credited'
          AND referrer_bonus_status IN ('pending', 'applying', 'credited')
          AND (
            referrer_managed_credential_ref IS NULL
            OR referrer_managed_credential_ref = ?
          )`,
    )
    .bind(input.referrerDiscordUserRef, input.managedCredentialRef)
    .first<{ count: number }>();

  return Number(row?.count ?? 0);
}

async function updateReferrerEntitlementBudget(
  db: D1Database,
  input: {
    referrerDiscordUserRef: string;
    installationId: string;
    managedCredentialRef: string;
    budgetUsd: number;
  },
): Promise<boolean> {
  const result = await db
    .prepare(
      `UPDATE openrouter_entitlements
          SET budget_usd = ?
        WHERE installation_id = ?
          AND discord_user_ref = ?
          AND status = 'active'
          AND discord_issue_status = 'active'
          AND managed_credential_ref = ?`,
    )
    .bind(
      input.budgetUsd,
      input.installationId,
      input.referrerDiscordUserRef,
      input.managedCredentialRef,
    )
    .run();

  return Number(result.meta.changes ?? 0) === 1;
}

async function markReferrerRewardRowsCredited(
  db: D1Database,
  input: {
    referrerDiscordUserRef: string;
    managedCredentialRef: string;
    nowIso: string;
  },
): Promise<number> {
  const result = await db
    .prepare(
      `UPDATE referral_rewards
          SET referrer_bonus_status = 'credited',
              referrer_managed_credential_ref = ?,
              failure_reason = NULL,
              updated_at = ?
        WHERE referrer_discord_user_ref = ?
          AND referred_bonus_status = 'credited'
          AND referrer_bonus_status = 'applying'
          AND (
            referrer_managed_credential_ref IS NULL
            OR referrer_managed_credential_ref = ?
          )`,
    )
    .bind(
      input.managedCredentialRef,
      input.nowIso,
      input.referrerDiscordUserRef,
      input.managedCredentialRef,
    )
    .run();

  return Number(result.meta.changes ?? 0);
}

async function markReferrerRewardRowsFailed(
  db: D1Database,
  input: {
    referrerDiscordUserRef: string;
    managedCredentialRef: string;
    nowIso: string;
    failureReason: 'referrer_patch_failed';
  },
): Promise<number> {
  assertIssueReferralFailureReason(input.failureReason);
  const result = await db
    .prepare(
      `UPDATE referral_rewards
          SET referrer_bonus_status = 'failed',
              referrer_managed_credential_ref = ?,
              failure_reason = ?,
              updated_at = ?
        WHERE referrer_discord_user_ref = ?
          AND referred_bonus_status = 'credited'
          AND referrer_bonus_status = 'applying'
          AND (
            referrer_managed_credential_ref IS NULL
            OR referrer_managed_credential_ref = ?
          )`,
    )
    .bind(
      input.managedCredentialRef,
      input.failureReason,
      input.nowIso,
      input.referrerDiscordUserRef,
      input.managedCredentialRef,
    )
    .run();

  const failedRows = Number(result.meta.changes ?? 0);
  if (failedRows > 0) {
    logReferralRewardOutcome({
      outcome: 'failed',
      referrerDiscordUserRef: input.referrerDiscordUserRef,
      referrerManagedCredentialRef: input.managedCredentialRef,
      reason: input.failureReason,
      affectedRows: failedRows,
    });
  }
  return failedRows;
}

async function markReferrerRewardRowsSkipped(
  db: D1Database,
  input: {
    referrerDiscordUserRef: string;
    nowIso: string;
    skipReason: 'referrer_managed_key_missing';
  },
): Promise<number> {
  const result = await db
    .prepare(
      `UPDATE referral_rewards
          SET referrer_bonus_status = 'skipped',
              skip_reason = ?,
              updated_at = ?
        WHERE referrer_discord_user_ref = ?
          AND referred_bonus_status = 'credited'
          AND referrer_bonus_status IN ('pending', 'applying')`,
    )
    .bind(input.skipReason, input.nowIso, input.referrerDiscordUserRef)
    .run();

  const skippedRows = Number(result.meta.changes ?? 0);
  if (skippedRows > 0) {
    logReferralRewardOutcome({
      outcome: 'skipped',
      referrerDiscordUserRef: input.referrerDiscordUserRef,
      reason: input.skipReason,
      affectedRows: skippedRows,
    });
  }
  return skippedRows;
}

async function listStaleReservedReferralRewards(
  db: D1Database,
  cutoffIso: string,
): Promise<ReferralRewardRecord[]> {
  const result = await db
    .prepare(
      `SELECT id,
              referral_id,
              referrer_discord_user_ref,
              referrer_installation_id,
              referred_discord_user_ref,
              referred_installation_id,
              referred_hardware_hash,
              referred_hardware_hash_salt_version,
              referred_bonus_status,
              referrer_bonus_status,
              skip_reason,
              failure_reason,
              referred_managed_credential_ref,
              referrer_managed_credential_ref,
              attempt_ip_hash,
              created_at,
              updated_at,
              credited_at
         FROM referral_rewards
        WHERE referred_bonus_status = 'reserved'
          AND updated_at < ?
        ORDER BY id ASC`,
    )
    .bind(cutoffIso)
    .all<ReferralRewardRecord>();
  return result.results;
}

async function getDeliveredReferredEntitlement(
  db: D1Database,
  reward: ReferralRewardRecord,
): Promise<OpenRouterEntitlementRecord | null> {
  const row = await db
    .prepare(
      `SELECT installation_id,
              status,
              budget_usd,
              managed_credential_ref,
              issued_at,
              expires_at,
              release_session_ref,
              release_token_hash,
              release_token_expires_at,
              verified_hardware_hash,
              verified_hardware_hash_salt_version,
              discord_user_ref,
              discord_issue_status,
              discord_issue_reserved_at,
              discord_issue_delivered_at
         FROM openrouter_entitlements
        WHERE installation_id = ?
          AND discord_user_ref = ?
          AND status = 'active'
          AND discord_issue_status = 'active'
          AND managed_credential_ref IS NOT NULL
          AND length(trim(managed_credential_ref)) > 0`,
    )
    .bind(reward.referred_installation_id, reward.referred_discord_user_ref)
    .first<OpenRouterEntitlementRecord>();

  if (!row) {
    return null;
  }

  if (
    reward.referred_managed_credential_ref &&
    reward.referred_managed_credential_ref !== row.managed_credential_ref
  ) {
    return null;
  }

  return row;
}

async function reconcileStaleReservedReferralToCredited(
  db: D1Database,
  input: {
    rewardId: number;
    referralId: string;
    referredInstallationId: string;
    referrerDiscordUserRef: string | null;
    managedCredentialRef: string;
    nowIso: string;
  },
): Promise<number> {
  const result = await db
    .prepare(
      `UPDATE referral_rewards
          SET referred_bonus_status = 'credited',
              referred_managed_credential_ref = ?,
              failure_reason = NULL,
              updated_at = ?,
              credited_at = COALESCE(credited_at, ?)
        WHERE id = ?
          AND referred_bonus_status = 'reserved'`,
    )
    .bind(input.managedCredentialRef, input.nowIso, input.nowIso, input.rewardId)
    .run();
  const changed = Number(result.meta.changes ?? 0);
  if (changed > 0) {
    logReferralRewardOutcome({
      outcome: 'credited',
      referralId: input.referralId,
      referredInstallationId: input.referredInstallationId,
      referrerDiscordUserRef: input.referrerDiscordUserRef,
      reason: 'stale_reserved_reconciled',
    });
  }
  return changed;
}

async function reconcileStaleReservedReferralToFailed(
  db: D1Database,
  input: {
    rewardId: number;
    referralId: string;
    referredInstallationId: string;
    referrerDiscordUserRef: string | null;
    nowIso: string;
  },
): Promise<number> {
  const failureReason = 'stale_reserved_reconciled';
  assertIssueReferralFailureReason(failureReason);
  const result = await db
    .prepare(
      `UPDATE referral_rewards
          SET referred_bonus_status = 'failed',
              referrer_bonus_status = 'failed',
              failure_reason = ?,
              updated_at = ?
        WHERE id = ?
          AND referred_bonus_status = 'reserved'`,
    )
    .bind(failureReason, input.nowIso, input.rewardId)
    .run();
  const changed = Number(result.meta.changes ?? 0);
  if (changed > 0) {
    logReferralRewardOutcome({
      outcome: 'failed',
      referralId: input.referralId,
      referredInstallationId: input.referredInstallationId,
      referrerDiscordUserRef: input.referrerDiscordUserRef,
      reason: failureReason,
    });
  }
  return changed;
}

async function requeueStaleApplyingReferralRewards(
  db: D1Database,
  input: { cutoffIso: string; nowIso: string },
): Promise<number> {
  const result = await db
    .prepare(
      `UPDATE referral_rewards
          SET referrer_bonus_status = 'pending',
              referrer_managed_credential_ref = NULL,
              failure_reason = NULL,
              updated_at = ?
        WHERE referred_bonus_status = 'credited'
          AND referrer_bonus_status = 'applying'
          AND updated_at < ?`,
    )
    .bind(input.nowIso, input.cutoffIso)
    .run();
  const changed = Number(result.meta.changes ?? 0);
  if (changed > 0) {
    logReferralRewardOutcome({
      outcome: 'pending',
      reason: 'stale_applying_requeued',
      affectedRows: changed,
    });
  }
  return changed;
}

async function deleteTerminalReferralRewardsOlderThan(
  db: D1Database,
  input: {
    referredBonusStatus: 'skipped' | 'failed';
    cutoffIso: string;
  },
): Promise<number> {
  const result = await db
    .prepare(
      `DELETE FROM referral_rewards
        WHERE referred_bonus_status = ?
          AND updated_at < ?`,
    )
    .bind(input.referredBonusStatus, input.cutoffIso)
    .run();
  return Number(result.meta.changes ?? 0);
}

function normalizeReferralDisableReason(value: unknown): ReferralDisableReason | null {
  if (typeof value !== 'string') {
    return null;
  }

  const normalized = value.trim();
  return (REFERRAL_DISABLE_REASONS as readonly string[]).includes(normalized)
    ? (normalized as ReferralDisableReason)
    : null;
}

function normalizeReferralDisableActor(value: unknown): string | null {
  if (typeof value !== 'string') {
    return null;
  }

  const normalized = value.trim();
  return REFERRAL_DISABLE_ACTOR_PATTERN.test(normalized) ? normalized : null;
}

async function appendReferralRuntimeAudit(
  db: D1Database,
  input: {
    eventKind: 'referral_id_disabled';
    reason: ReferralDisableReason;
    payload: Record<string, unknown>;
    createdAt: string;
  },
): Promise<void> {
  await db
    .prepare(
      `INSERT INTO broker_abuse_runtime_audit (
          event_kind,
          reason,
          payload_json,
          created_at
        ) VALUES (?, ?, ?, ?)`,
    )
    .bind(
      input.eventKind,
      input.reason,
      JSON.stringify(input.payload),
      input.createdAt,
    )
    .run();
}

function referrerRewardTargetLimitUsd(reflectedRewardCount: number): number {
  return usdFromCents(
    currencyCents(MANAGED_TRIAL_BUDGET_POLICY.hardLimit) +
      reflectedRewardCount * REFERRER_REFERRAL_REWARD_CENTS,
  );
}

function maxUsd(...values: number[]): number {
  return usdFromCents(Math.max(...values.map(currencyCents)));
}

function currencyCents(value: number): number {
  if (!Number.isFinite(value) || value < 0) {
    throw new Error('managed budget must be a finite non-negative USD value');
  }

  return Math.round(value * USD_CENTS);
}

function usdFromCents(cents: number): number {
  return Number((cents / USD_CENTS).toFixed(2));
}

function assertIssueReferralSkipReason(reason: IssueReferralSkipReason): void {
  if (!(ISSUE_REFERRAL_SKIP_REASONS as readonly string[]).includes(reason)) {
    throw new Error('unbounded issue referral skip reason');
  }
}

function assertIssueReferralFailureReason(reason: IssueReferralFailureReason): void {
  if (!(ISSUE_REFERRAL_FAILURE_REASONS as readonly string[]).includes(reason)) {
    throw new Error('unbounded issue referral failure reason');
  }
}

function logReferralRewardOutcome(input: {
  outcome:
    | 'reserved'
    | 'skipped'
    | 'failed'
    | 'applying'
    | 'pending'
    | 'credited'
    | 'disabled';
  referralId?: string | null;
  referredInstallationId?: string | null;
  referrerDiscordUserRef?: string | null;
  referrerManagedCredentialRef?: string | null;
  reason?: string | null;
  affectedRows?: number;
}): void {
  const payload: Record<string, string | number | null> = {
    outcome: input.outcome,
    broker_timestamp: new Date().toISOString(),
  };

  if (input.referralId) {
    payload.referral_id = input.referralId;
  }
  if (input.referredInstallationId) {
    payload.referred_installation_id = input.referredInstallationId;
  }
  if (input.referrerDiscordUserRef) {
    payload.referrer_discord_user_ref = input.referrerDiscordUserRef;
  }
  if (input.referrerManagedCredentialRef) {
    payload.referrer_managed_credential_ref = input.referrerManagedCredentialRef;
  }
  if (input.reason) {
    payload.reason = boundLogReason(input.reason);
  }
  if (input.affectedRows !== undefined) {
    payload.affected_rows = input.affectedRows;
  }

  console.info(REFERRAL_REWARD_LOG_EVENT, payload);
}

function boundLogReason(reason: string): string {
  const normalized = reason.trim();
  if (/^[a-z0-9_:-]{1,64}$/u.test(normalized)) {
    return normalized;
  }

  return 'unclassified';
}

export async function ensureOwnedReferralIdForActiveDiscordManagedUser(
  db: D1Database,
  input: {
    installationId: string;
    nowIso: string;
    generateReferralId?: ReferralIdGenerator;
    maxCollisionAttempts?: number;
  },
): Promise<OwnedReferralIdEnsureResult> {
  const owner = await getActiveDiscordManagedReferralOwner(
    db,
    input.nowIso,
    input.installationId,
  );
  if (!owner) {
    return { ok: false, reason: 'not_eligible' };
  }

  const discordUserRef = owner.discord_user_ref.trim();
  if (!isPersistableOwnedDiscordUserRef(discordUserRef)) {
    return { ok: false, reason: 'unsafe_discord_user_ref' };
  }

  const existing = await getReferralCodeForDiscordUserRef(db, discordUserRef);
  if (existing) {
    if (existing.status === 'disabled') {
      return { ok: false, reason: 'disabled' };
    }

    const refreshed = await refreshActiveReferralCodeOwnerInstallation(db, {
      referralId: existing.referral_id,
      discordUserRef,
      installationId: owner.installation_id,
      nowIso: input.nowIso,
    });
    if (!refreshed) {
      const latest = await getReferralCodeForDiscordUserRef(db, discordUserRef);
      if (latest?.status === 'disabled') {
        return { ok: false, reason: 'disabled' };
      }

      return { ok: false, reason: 'not_eligible' };
    }

    return {
      ok: true,
      referralCode: refreshed,
      created: false,
    };
  }

  const createReferralId = input.generateReferralId ?? generateReferralId;
  const maxCollisionAttempts =
    input.maxCollisionAttempts ?? DEFAULT_REFERRAL_ID_COLLISION_ATTEMPTS;

  for (let attempt = 0; attempt < maxCollisionAttempts; attempt += 1) {
    const referralId = normalizeReferralId(createReferralId());
    if (!referralId) {
      throw new Error('generated Referral ID did not match the approved format');
    }

    const inserted = await insertActiveOwnedReferralCode(db, {
      referralId,
      discordUserRef,
      installationId: owner.installation_id,
      nowIso: input.nowIso,
    });
    if (inserted) {
      const created = await getActiveReferralCodeForDiscordUserRef(db, discordUserRef);
      if (!created) {
        const latest = await getReferralCodeForDiscordUserRef(db, discordUserRef);
        if (latest?.status === 'disabled') {
          return { ok: false, reason: 'disabled' };
        }

        throw new Error('created Referral ID could not be read back as active');
      }
      return { ok: true, referralCode: created, created: true };
    }

    const concurrentlyCreated = await getReferralCodeForDiscordUserRef(
      db,
      discordUserRef,
    );
    if (concurrentlyCreated) {
      if (concurrentlyCreated.status === 'disabled') {
        return { ok: false, reason: 'disabled' };
      }
      return {
        ok: true,
        referralCode: concurrentlyCreated,
        created: false,
      };
    }
  }

  return { ok: false, reason: 'collision_exhausted' };
}

function cryptoReferralRandomBytes(byteLength: number): Uint8Array {
  const bytes = new Uint8Array(byteLength);
  crypto.getRandomValues(bytes);
  return bytes;
}

async function getActiveDiscordManagedReferralOwner(
  db: D1Database,
  nowIso: string,
  installationId: string,
): Promise<ActiveDiscordManagedReferralOwner | null> {
  const row = await db
    .prepare(
      `SELECT entitlement.installation_id,
              entitlement.status,
              entitlement.budget_usd,
              entitlement.managed_credential_ref,
              entitlement.issued_at,
              entitlement.expires_at,
              entitlement.release_session_ref,
              entitlement.release_token_hash,
              entitlement.release_token_expires_at,
              entitlement.verified_hardware_hash,
              entitlement.verified_hardware_hash_salt_version,
              entitlement.discord_user_ref,
              entitlement.discord_issue_status,
              entitlement.discord_issue_reserved_at,
              entitlement.discord_issue_delivered_at
         FROM openrouter_entitlements entitlement
         JOIN discord_identities identity
           ON identity.discord_user_ref = entitlement.discord_user_ref
        WHERE entitlement.installation_id = ?
          AND entitlement.status = 'active'
          AND entitlement.discord_user_ref IS NOT NULL
          AND length(trim(entitlement.discord_user_ref)) > 0
          AND entitlement.managed_credential_ref IS NOT NULL
          AND length(trim(entitlement.managed_credential_ref)) > 0
          AND entitlement.expires_at IS NOT NULL
          AND length(trim(entitlement.expires_at)) > 0
          AND entitlement.discord_issue_status = 'active'
          AND entitlement.discord_issue_delivered_at IS NOT NULL
          AND length(trim(entitlement.discord_issue_delivered_at)) > 0
          AND identity.status = 'active'
          AND identity.entitlement_installation_id = entitlement.installation_id`,
    )
    .bind(installationId)
    .first<ActiveDiscordManagedReferralOwner>();

  if (!row) {
    return null;
  }

  const now = new Date(nowIso);
  if (Number.isNaN(now.getTime())) {
    return null;
  }

  return resolveEffectiveEntitlementLifecycle(row, now) === 'active' ? row : null;
}

function isPersistableOwnedDiscordUserRef(value: string): boolean {
  return OWNED_DISCORD_USER_REF_PATTERN.test(value);
}

async function getReferralCodeForDiscordUserRef(
  db: D1Database,
  discordUserRef: string,
): Promise<ReferralCodeRecord | null> {
  return db
    .prepare(
      `SELECT referral_id,
              owner_discord_user_ref,
              owner_installation_id,
              status,
              created_at,
              updated_at
         FROM referral_codes
        WHERE owner_discord_user_ref = ?`,
    )
    .bind(discordUserRef)
    .first<ReferralCodeRecord>();
}

async function getActiveReferralCodeForDiscordUserRef(
  db: D1Database,
  discordUserRef: string,
): Promise<ReferralCodeRecord | null> {
  return db
    .prepare(
      `SELECT referral_id,
              owner_discord_user_ref,
              owner_installation_id,
              status,
              created_at,
              updated_at
         FROM referral_codes
        WHERE owner_discord_user_ref = ?
          AND status = 'active'`,
    )
    .bind(discordUserRef)
    .first<ReferralCodeRecord>();
}

async function refreshActiveReferralCodeOwnerInstallation(
  db: D1Database,
  input: {
    referralId: string;
    discordUserRef: string;
    installationId: string;
    nowIso: string;
  },
): Promise<ReferralCodeRecord | null> {
  await db
    .prepare(
      `UPDATE referral_codes
          SET owner_installation_id = ?,
              updated_at = ?
        WHERE referral_id = ?
          AND owner_discord_user_ref = ?
          AND status = 'active'
          AND (owner_installation_id IS NULL OR owner_installation_id <> ?)`,
    )
    .bind(
      input.installationId,
      input.nowIso,
      input.referralId,
      input.discordUserRef,
      input.installationId,
    )
    .run();

  return getActiveReferralCodeForDiscordUserRef(db, input.discordUserRef);
}

async function insertActiveOwnedReferralCode(
  db: D1Database,
  input: {
    referralId: string;
    discordUserRef: string;
    installationId: string;
    nowIso: string;
  },
): Promise<boolean> {
  const result = await db
    .prepare(
      `INSERT OR IGNORE INTO referral_codes (
          referral_id,
          owner_discord_user_ref,
          owner_installation_id,
          status,
          created_at,
          updated_at
        ) VALUES (?, ?, ?, 'active', ?, ?)`,
    )
    .bind(
      input.referralId,
      input.discordUserRef,
      input.installationId,
      input.nowIso,
      input.nowIso,
    )
    .run();

  return Number(result.meta.changes ?? 0) === 1;
}

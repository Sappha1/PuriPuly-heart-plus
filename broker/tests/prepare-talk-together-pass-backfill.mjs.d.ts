declare module '*/prepare-talk-together-pass-backfill.mjs' {
  export const TALK_TOGETHER_PASS_BACKFILL_QUERIES: Readonly<{
    existingReferralIds: string;
    realRefCandidates: string;
    syntheticLegacyCandidates: string;
  }>;

  export interface RealRefBackfillRow {
    installation_id: string;
    discord_user_ref: string;
  }

  export interface LegacyBackfillRow {
    installation_id: string;
    issued_at?: string;
    expires_at?: string;
    verified_hardware_hash?: string;
    verified_hardware_hash_salt_version?: number | string;
  }

  export interface BackfillSqlInput {
    nowIso: string;
    existingReferralIds?: string[];
    realRefRows?: RealRefBackfillRow[];
    legacyRows?: LegacyBackfillRow[];
    maxLegacy: number | string;
    randomBytes?: (size: number) => Uint8Array;
  }

  export interface BackfillSqlSummary {
    realRefRows: number;
    legacyRows: number;
    generatedReferralCodes: number;
    syntheticIdentityRows: number;
    existingReferralIds: number;
  }

  export function allocateReferralId(
    existingCodes: Set<string>,
    randomBytesFn?: (size: number) => Uint8Array,
  ): string;
  export function syntheticDiscordUserRef(installationId: string): string;
  export function buildBackfillSql(input: BackfillSqlInput): {
    sql: string;
    summary: BackfillSqlSummary;
  };
  export function validateGeneratedSql(sql: string): void;
  export function extractD1Rows(value: unknown): Array<Record<string, unknown>>;
}

import { expect } from 'vitest';

const FORBIDDEN_REFERRAL_ESTIMATE_RESPONSE_KEYS = [
  'referral_reward_usd',
  'referral_bonus_usd',
  'referral_bonus_amount_usd',
  'referral_reward_amount_usd',
  'referral_estimated_utterances',
  'estimated_utterances',
];
const FORBIDDEN_REFERRAL_ESTIMATE_RESPONSE_KEY_SET = new Set(
  FORBIDDEN_REFERRAL_ESTIMATE_RESPONSE_KEYS.map(normalizeResponsePathFragment),
);
const REFERRAL_REWARD_HINT_PATTERN = /(referral|reward|bonus)/u;
const MONEY_HINT_PATTERN = /(usd|dollar|cent|cents|amount|value)/u;
const UTTERANCE_HINT_PATTERN = /utterance/u;
const ESTIMATE_HINT_PATTERN = /(estimate|estimated|count)/u;

export function expectNoReferralRewardEstimateFields(value: unknown): void {
  expect(collectForbiddenReferralRewardEstimatePaths(value)).toEqual([]);
}

function collectForbiddenReferralRewardEstimatePaths(
  value: unknown,
  path: string[] = [],
): string[] {
  if (!value || typeof value !== 'object') {
    return [];
  }

  if (Array.isArray(value)) {
    return value.flatMap((entry, index) =>
      collectForbiddenReferralRewardEstimatePaths(entry, [...path, `[${index}]`]),
    );
  }

  return Object.entries(value).flatMap(([key, nested]) => {
    const currentPath = [...path, key];
    const nestedForbiddenPaths = collectForbiddenReferralRewardEstimatePaths(
      nested,
      currentPath,
    );
    return isForbiddenReferralRewardEstimatePath(currentPath)
      ? [currentPath.join('.'), ...nestedForbiddenPaths]
      : nestedForbiddenPaths;
  });
}

function isForbiddenReferralRewardEstimatePath(path: string[]): boolean {
  const key = path.at(-1) ?? '';
  const normalizedKey = normalizeResponsePathFragment(key);
  if (FORBIDDEN_REFERRAL_ESTIMATE_RESPONSE_KEY_SET.has(normalizedKey)) {
    return true;
  }

  const normalizedPath = normalizeResponsePathFragment(path.join('.'));
  if (
    REFERRAL_REWARD_HINT_PATTERN.test(normalizedPath) &&
    MONEY_HINT_PATTERN.test(normalizedPath)
  ) {
    return true;
  }

  return (
    UTTERANCE_HINT_PATTERN.test(normalizedPath) &&
    (REFERRAL_REWARD_HINT_PATTERN.test(normalizedPath) ||
      ESTIMATE_HINT_PATTERN.test(normalizedPath))
  );
}

function normalizeResponsePathFragment(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]/gu, '');
}

import { BROKER_PUBLIC_INPUT_BOUNDS } from './persistence';

const CONTROL_CHARACTER_PATTERN = /\p{Cc}/u;
const NEWLINE_PATTERN = /[\r\n\u0085\u2028\u2029]/u;
const countCharacters = (value: string): number => Array.from(value).length;
const isWhitespaceOnly = (value: string): boolean => value.trim().length === 0;

export function stringValue(value: unknown): string | null {
  return typeof value === 'string' ? value : null;
}

export function validatePublicInput(
  field: keyof typeof BROKER_PUBLIC_INPUT_BOUNDS,
  value: string,
): string | null {
  const bounds = BROKER_PUBLIC_INPUT_BOUNDS[field];

  if (bounds.rejectWhitespaceOnly && isWhitespaceOnly(value)) {
    return `${field} must not be blank or whitespace-only`;
  }

  const characterCount = countCharacters(value);

  if (characterCount < bounds.minLength || characterCount > bounds.maxLength) {
    return `${field} must be between ${bounds.minLength} and ${bounds.maxLength} characters`;
  }

  const hasDisallowedControlOrNewline =
    (bounds.rejectControlCharacters && CONTROL_CHARACTER_PATTERN.test(value)) ||
    (bounds.rejectNewlines && NEWLINE_PATTERN.test(value));

  if (hasDisallowedControlOrNewline) {
    return `${field} must not contain control characters or newlines`;
  }

  return null;
}

export function nonEmptyString(value: unknown): string | null {
  return typeof value === 'string' && value.trim().length > 0 ? value : null;
}

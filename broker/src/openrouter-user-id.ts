const OPENROUTER_USER_ID_VERSION = 'v1';
const OPENROUTER_USER_ID_PREFIX = `ph-or-user-${OPENROUTER_USER_ID_VERSION}_`;
const OPENROUTER_USER_ID_PAYLOAD_PREFIX =
  `puripuly-heart:openrouter-user:${OPENROUTER_USER_ID_VERSION}`;

export interface DeriveManagedOpenRouterUserIdInput {
  installationId: string;
  secret: string;
}

export async function deriveManagedOpenRouterUserId({
  installationId,
  secret,
}: DeriveManagedOpenRouterUserIdInput): Promise<string | null> {
  const normalizedInstallationId = installationId.trim();
  const normalizedSecret = secret.trim();

  if (!normalizedInstallationId || !normalizedSecret) {
    return null;
  }

  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    'raw',
    encoder.encode(normalizedSecret),
    {
      name: 'HMAC',
      hash: 'SHA-256',
    },
    false,
    ['sign'],
  );
  const signature = await crypto.subtle.sign(
    'HMAC',
    key,
    encoder.encode(
      `${OPENROUTER_USER_ID_PAYLOAD_PREFIX}\n${normalizedInstallationId}`,
    ),
  );

  return `${OPENROUTER_USER_ID_PREFIX}${toBase64Url(signature)}`;
}

function toBase64Url(value: ArrayBuffer): string {
  let binary = '';

  for (const byte of new Uint8Array(value)) {
    binary += String.fromCharCode(byte);
  }

  return btoa(binary).replace(/\+/gu, '-').replace(/\//gu, '_').replace(/=+$/u, '');
}

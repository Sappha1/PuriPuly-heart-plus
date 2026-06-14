import { readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const CANONICAL_WORKER_NAME = 'puripuly-heart-broker';
const DATABASE_ID_PLACEHOLDER = 'REQUIRED_AT_DEPLOY_TIME';

await main();

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const sourcePath = resolve(
    args.source ?? fileURLToPath(new URL('../wrangler.jsonc', import.meta.url)),
  );
  const outputPath = resolve(requiredArg(args, 'out'));
  const databaseId = requiredArg(args, 'database-id');
  const sourceText = await readFile(sourcePath, 'utf8');
  const nameMatch = sourceText.match(/"name"\s*:\s*"([^"]+)"/u);

  if (!nameMatch) {
    throw new Error('wrangler config is missing a worker name field');
  }

  if (nameMatch[1] !== CANONICAL_WORKER_NAME) {
    throw new Error(
      `wrangler config must keep the canonical worker name ${CANONICAL_WORKER_NAME}`,
    );
  }

  const databaseIdPlaceholderPattern = new RegExp(
    `"database_id"\\s*:\\s*"${DATABASE_ID_PLACEHOLDER}"`,
    'gu',
  );
  const placeholderMatches = sourceText.match(databaseIdPlaceholderPattern) ?? [];

  if (placeholderMatches.length !== 1) {
    throw new Error(
      `expected exactly one ${DATABASE_ID_PLACEHOLDER} database_id placeholder`,
    );
  }

  const renderedConfig = sourceText.replace(
    databaseIdPlaceholderPattern,
    `"database_id": ${JSON.stringify(databaseId)}`,
  );

  await writeFile(outputPath, renderedConfig, 'utf8');
  process.stdout.write(`${outputPath}\n`);
}

function parseArgs(argv) {
  const args = {};

  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];

    if (!token?.startsWith('--')) {
      throw new Error(`unexpected argument: ${token ?? '<missing>'}`);
    }

    const key = token.slice(2);
    const value = argv[index + 1];

    if (!value || value.startsWith('--')) {
      throw new Error(`missing value for --${key}`);
    }

    args[key] = value;
    index += 1;
  }

  return args;
}

function requiredArg(args, key) {
  const value = args[key];

  if (!value) {
    throw new Error(`missing required --${key} argument`);
  }

  return value;
}

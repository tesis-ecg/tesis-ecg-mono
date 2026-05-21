import { execSync } from 'child_process';
import { readFileSync } from 'fs';
import path from 'path';

/**
 * Surgical format hook:
 * Only runs formatting on the specific file that was just modified.
 * - TypeScript/JavaScript: prettier (front/)
 * - Python: ruff format (back/)
 */
try {
  const raw = readFileSync(0, 'utf-8');
  const payload = JSON.parse(raw);
  const filePath = payload?.tool_input?.file_path;

  if (!filePath) {
    console.log(JSON.stringify({ decision: 'allow', reason: 'No file path' }));
    process.exit(0);
  }

  const resolvedFilePath = path.isAbsolute(filePath)
    ? filePath
    : path.join(process.cwd(), filePath);

  const isTS = filePath.endsWith('.ts') || filePath.endsWith('.tsx') ||
               filePath.endsWith('.js') || filePath.endsWith('.jsx');
  const isPython = filePath.endsWith('.py');

  if (isTS) {
    const appDir = resolvedFilePath.includes('apps/backend')
      ? 'apps/backend'
      : resolvedFilePath.includes('apps/frontend')
        ? 'apps/frontend'
        : null;
    const cwd = appDir ? path.join(process.cwd(), appDir) : process.cwd();
    const relativePath = appDir ? path.relative(cwd, resolvedFilePath) : resolvedFilePath;

    console.error(`--- Formatting (prettier): ${filePath} ---`);
    execSync(`npx prettier --write "${relativePath}"`, { cwd, stdio: 'inherit' });

  } else if (isPython) {
    const backDir = path.join(process.cwd(), 'back');
    const relativePath = path.relative(backDir, resolvedFilePath);

    console.error(`--- Formatting (ruff): ${filePath} ---`);
    execSync(`uv run ruff format "${relativePath}"`, { cwd: backDir, stdio: 'inherit' });
    execSync(`uv run ruff check --fix "${relativePath}"`, { cwd: backDir, stdio: 'inherit' });
  }

  console.log(JSON.stringify({
    decision: 'allow',
    reason: 'Surgical formatting complete',
  }));
} catch (error) {
  console.error(`Surgical Format Hook Failed: ${error.message}`);
  process.exit(0);
}

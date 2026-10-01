import { execFileSync } from 'child_process';
import { readFileSync, realpathSync, existsSync } from 'fs';
import path from 'path';

// Format only the edited product file with the owning workspace's configuration.
try {
  const payload = JSON.parse(readFileSync(0, 'utf-8'));
  const filePath = payload?.tool_input?.file_path;
  if (!filePath) process.exit(0);

  const root = realpathSync(process.cwd());
  const resolved = realpathSync(path.resolve(root, filePath));
  const relative = path.relative(root, resolved);
  const parts = relative.split(path.sep);
  if (path.isAbsolute(relative) || parts.includes('..') ||
      parts.some((part) => ['node_modules', '.venv', 'dist', 'build'].includes(part))) {
    process.exit(0);
  }

  const workspace = parts[0];
  const cwd = path.join(root, workspace);
  const target = path.relative(cwd, resolved);
  if (['front', 'mobile'].includes(workspace) && /\.(ts|tsx|js|jsx)$/.test(resolved)) {
    // A workspace without Prettier (currently mobile) keeps its existing toolchain.
    if (!existsSync(path.join(cwd, 'node_modules/.bin/prettier'))) process.exit(0);
    console.error(`Formatting (prettier): ${relative}`);
    execFileSync('npx', ['--no-install', 'prettier', '--write', '--', target], {
      cwd, stdio: ['ignore', 'ignore', 'inherit'],
    });
  } else if (workspace === 'back' && resolved.endsWith('.py')) {
    console.error(`Formatting (ruff): ${relative}`);
    execFileSync('uv', ['run', 'ruff', 'format', '--', target], {
      cwd, stdio: ['ignore', 'ignore', 'inherit'],
    });
    execFileSync('uv', ['run', 'ruff', 'check', '--fix', '--', target], {
      cwd, stdio: ['ignore', 'ignore', 'inherit'],
    });
  }
} catch (error) {
  console.error(`Surgical Format Hook Failed: ${error.message}`);
  process.exit(0);
}

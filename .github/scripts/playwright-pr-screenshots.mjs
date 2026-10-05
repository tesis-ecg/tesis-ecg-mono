#!/usr/bin/env node
// Put the screenshots from a green Playwright run into the PR description.
//
// Specs call prScreenshot() (front/e2e/support/pr-screenshot.ts), which
// writes <slug>.png plus <slug>.json ({ caption, spec }). The Playwright job
// uploads them; playwright-report.yml downloads them and runs this script with
// main's code and a write token. Only screenshots from specs the PR added or
// changed are published, so an unrelated PR never inherits another flow's
// pictures. The images go to the `pr-screenshots` branch (one parentless
// commit, force-pushed, so its history never grows) under pr-<n>/<sha>/, and the
// PR body gets a "Screenshots" section between two markers, replaced in place.
//
// Everything in the artifact was produced by the PR's own code: file names are
// rebuilt, captions are reduced to plain text, and only real PNGs under the size
// limit are published. The repository is public, so the images on that branch are
// too: that is why specs may only show the demo seed's synthetic data.
//
// Env: SCREENSHOT_DIR, RUN_CONCLUSION, PR_NUMBER, HEAD_SHA, GITHUB_TOKEN,
// GITHUB_REPOSITORY, GITHUB_SERVER_URL.

import { execFileSync } from 'node:child_process';
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const BRANCH = 'pr-screenshots';
const START = '<!-- pr-screenshots:start -->';
const END = '<!-- pr-screenshots:end -->';
const MAX_IMAGES = 12;
const MAX_BYTES = 3 * 1024 * 1024;
const PNG_MAGIC = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

const {
  SCREENSHOT_DIR,
  RUN_CONCLUSION,
  PR_NUMBER,
  HEAD_SHA = '',
  GITHUB_TOKEN,
  GITHUB_REPOSITORY,
  GITHUB_SERVER_URL = 'https://github.com',
} = process.env;

if (RUN_CONCLUSION !== 'success') {
  console.log(`Playwright run concluded "${RUN_CONCLUSION}": screenshots are only published for a green run.`);
  process.exit(0);
}
if (!/^\d+$/.test(PR_NUMBER ?? '') || !/^[0-9a-f]{40}$/.test(HEAD_SHA)) {
  console.log('::error::PR_NUMBER and HEAD_SHA are required.');
  process.exit(1);
}
const sha = HEAD_SHA.slice(0, 7);

async function github(path, init = {}) {
  const res = await fetch(`https://api.github.com${path}`, {
    ...init,
    headers: {
      accept: 'application/vnd.github+json',
      authorization: `Bearer ${GITHUB_TOKEN}`,
      ...(init.body ? { 'content-type': 'application/json' } : {}),
    },
  });
  if (!res.ok) throw new Error(`${init.method ?? 'GET'} ${path} -> HTTP ${res.status}`);
  return res.status === 204 ? null : res.json();
}

// The path is printed in the PR body, so only plain path characters qualify: a
// spec named with a backtick or an @ gets no screenshots instead of injecting
// Markdown or a mention.
async function changedSpecs() {
  const specs = new Set();
  for (let page = 1; ; page += 1) {
    const rows = await github(`/repos/${GITHUB_REPOSITORY}/pulls/${PR_NUMBER}/files?per_page=100&page=${page}`);
    for (const f of rows) {
      const m = /^front\/e2e\/([\w./-]+\.spec\.ts)$/.exec(f.filename);
      if (m && f.status !== 'removed') specs.add(m[1]);
    }
    if (rows.length < 100) return specs;
  }
}

// Plain text only: no markdown, HTML or @mentions can come out of a caption.
function plainCaption(raw) {
  const text = String(raw ?? '')
    .replace(/[^\p{L}\p{N} ,.:;()'/&+-]/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 100);
  return text || 'Screenshot';
}

function collect(specs) {
  if (!SCREENSHOT_DIR || !existsSync(SCREENSHOT_DIR)) return { shots: [], skipped: 0 };
  const shots = [];
  for (const name of readdirSync(SCREENSHOT_DIR).sort()) {
    if (!name.endsWith('.json')) continue;
    const base = name.slice(0, -5);
    const png = join(SCREENSHOT_DIR, `${base}.png`);
    let meta;
    try {
      meta = JSON.parse(readFileSync(join(SCREENSHOT_DIR, name), 'utf8'));
    } catch {
      continue;
    }
    if (typeof meta?.spec !== 'string' || !specs.has(meta.spec)) continue;
    if (!existsSync(png) || statSync(png).size > MAX_BYTES) continue;
    if (!readFileSync(png).subarray(0, 8).equals(PNG_MAGIC)) continue;
    shots.push({ png, spec: meta.spec, caption: plainCaption(meta.caption), slug: base.replace(/[^a-z0-9-]/g, '').slice(0, 80) || 'shot' });
  }
  shots.sort((a, b) => a.spec.localeCompare(b.spec) || a.slug.localeCompare(b.slug));
  const skipped = Math.max(0, shots.length - MAX_IMAGES);
  return { shots: shots.slice(0, MAX_IMAGES), skipped };
}

function git(cwd, args, opts = {}) {
  const auth = Buffer.from(`x-access-token:${GITHUB_TOKEN}`).toString('base64');
  return execFileSync(
    'git',
    [
      '-c', `http.extraheader=AUTHORIZATION: basic ${auth}`,
      '-c', 'user.name=github-actions[bot]',
      '-c', 'user.email=41898282+github-actions[bot]@users.noreply.github.com',
      ...args,
    ],
    { cwd, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'], ...opts },
  ).trim();
}

// Replace pr-<n>/ on the branch with this commit's images. Parentless commit +
// lease-protected force push; a concurrent publish for another PR makes the
// lease fail, and the loop starts again from the branch's new tip.
function publish(shots) {
  const files = shots.map((s, i) => ({ ...s, file: `${String(i + 1).padStart(2, '0')}-${s.slug}.png` }));
  for (let attempt = 1; attempt <= 5; attempt += 1) {
    const dir = mkdtempSync(join(tmpdir(), 'pr-shots-'));
    try {
      git(dir, ['init', '-q']);
      git(dir, ['remote', 'add', 'origin', `${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}.git`]);
      let tip = '';
      try {
        git(dir, ['fetch', '-q', '--depth', '1', 'origin', `refs/heads/${BRANCH}`]);
        tip = git(dir, ['rev-parse', 'FETCH_HEAD']);
        git(dir, ['checkout', '-q', '--detach', tip]);
      } catch {
        tip = ''; // The branch does not exist yet.
      }
      rmSync(join(dir, `pr-${PR_NUMBER}`), { recursive: true, force: true });
      const target = join(dir, `pr-${PR_NUMBER}`, sha);
      mkdirSync(target, { recursive: true });
      for (const f of files) copyFileSync(f.png, join(target, f.file));

      git(dir, ['add', '-A']);
      const tree = git(dir, ['write-tree']);
      const commit = git(dir, ['commit-tree', tree, '-m', `Screenshots for #${PR_NUMBER} at ${sha}`]);
      git(dir, ['push', '-q', `--force-with-lease=refs/heads/${BRANCH}:${tip}`, 'origin', `${commit}:refs/heads/${BRANCH}`]);
      return files;
    } catch (err) {
      console.log(`Publishing attempt ${attempt} failed: ${String(err.stderr || err.message).split('\n')[0]}`);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  }
  throw new Error(`could not push to ${BRANCH} after 5 attempts`);
}

function section(files, skipped) {
  const url = (file) => `${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}/blob/${BRANCH}/pr-${PR_NUMBER}/${sha}/${file}?raw=true`;
  const lines = [
    START,
    '## Screenshots',
    '',
    `Taken by the Playwright e2e check on \`${sha}\`, with demo data and stubbed API responses.`,
    '',
  ];
  for (const f of files) {
    lines.push(`**${f.caption}** (\`front/e2e/${f.spec}\`)`, '', `![${f.caption}](${url(f.file)})`, '');
  }
  if (skipped) lines.push(`${skipped} more screenshot(s) not shown (limit ${MAX_IMAGES}).`, '');
  lines.push(END);
  return lines.join('\n');
}

function withSection(body, next) {
  const text = body ?? '';
  const start = text.indexOf(START);
  const end = text.indexOf(END);
  const rest = start !== -1 && end > start ? `${text.slice(0, start).trimEnd()}\n\n${text.slice(end + END.length).trimStart()}`.trim() : text.trim();
  if (!next) return rest;
  // Above the closing "Generated with" line when there is one, otherwise at the end.
  const footer = rest.lastIndexOf('\n🤖 Generated with');
  return footer === -1 ? `${rest}\n\n${next}` : `${rest.slice(0, footer).trimEnd()}\n\n${next}\n${rest.slice(footer)}`;
}

const pr = await github(`/repos/${GITHUB_REPOSITORY}/pulls/${PR_NUMBER}`);
if (pr.head.sha !== HEAD_SHA) {
  console.log(`PR #${PR_NUMBER} moved on to ${pr.head.sha.slice(0, 7)}; that run will publish its own screenshots.`);
  process.exit(0);
}

const { shots, skipped } = collect(await changedSpecs());
let body;
if (shots.length === 0) {
  console.log('No screenshots from specs this PR changed.');
  body = withSection(pr.body, null);
} else {
  const files = publish(shots);
  console.log(`Published ${files.length} screenshot(s) to ${BRANCH}/pr-${PR_NUMBER}/${sha}.`);
  if (skipped) console.log(`::warning::${skipped} screenshot(s) over the limit of ${MAX_IMAGES} were not published.`);
  body = withSection(pr.body, section(files, skipped));
}

if (body !== (pr.body ?? '').trim()) {
  await github(`/repos/${GITHUB_REPOSITORY}/pulls/${PR_NUMBER}`, { method: 'PATCH', body: JSON.stringify({ body }) });
  console.log('PR description updated.');
}

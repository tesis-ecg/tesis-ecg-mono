#!/usr/bin/env node
// Turn the Playwright JSON report into one sticky PR comment and the job summary.
//
// Agents working on a PR read PR comments but not Actions logs or check
// annotations, so a failing spec is only fixable if the failure is written
// somewhere they can read. One comment per PR, edited in place:
//   - the job failed:  the failed tests and their errors (or "did not finish")
//   - the job passed:  the existing comment, if any, flips to "passed"; a PR
//                      that never failed gets no comment at all
//   - the job was cancelled (a newer push superseded it): nothing
//   - the PR has moved on to a newer commit: nothing, so a late report for an
//     old commit never overwrites the newer run's result
//
// It runs in playwright-report.yml (main's code, after the Playwright run) on
// the report the PR's own code produced, so error text is untrusted: the test
// user's E2E_* values are redacted (GitHub masks secrets in logs, not in comment
// bodies), and @mentions and code fences are defused so a spec can't ping
// anyone or break out of a block.
//
// Env: RESULTS_FILE, JOB_STATUS (the Playwright run's conclusion), RUN_URL,
// PR_NUMBER, HEAD_SHA, GITHUB_TOKEN, GITHUB_REPOSITORY.

import { appendFileSync, existsSync, readFileSync } from 'node:fs';

const MARKER = '<!-- playwright-ci -->';
const MAX_ERROR_CHARS = 3000;
const MAX_BODY_CHARS = 60000;

const {
  RESULTS_FILE,
  JOB_STATUS,
  RUN_URL: runUrl = '',
  PR_NUMBER,
  HEAD_SHA = '',
  GITHUB_TOKEN,
  GITHUB_REPOSITORY,
  GITHUB_STEP_SUMMARY,
} = process.env;

if (JOB_STATUS === 'cancelled' || JOB_STATUS === 'skipped') process.exit(0);

const sha = HEAD_SHA.slice(0, 7);
const secrets = ['E2E_PASSWORD', 'E2E_EMAIL', 'E2E_AUTH0_SUB']
  .map((name) => process.env[name]?.trim())
  .filter((value) => value && value.length >= 4);

function clean(text) {
  // eslint-disable-next-line no-control-regex
  let out = text.replace(/\u001b\[[0-9;]*m/g, '');
  for (const secret of secrets) out = out.split(secret).join('[redacted]');
  return out.replace(/@(?=[\w-])/g, '@\u200b').replace(/```/g, "'''");
}

function collectTests(suites, out = []) {
  for (const suite of suites ?? []) {
    for (const spec of suite.specs ?? []) {
      for (const test of spec.tests ?? []) out.push({ spec, test });
    }
    collectTests(suite.suites, out);
  }
  return out;
}

function errorText(test) {
  const last = test.results?.at(-1);
  const errors = last?.errors?.length ? last.errors : last?.error ? [last.error] : [];
  const text = errors.map((e) => e.message ?? e.value ?? '').join('\n\n').trim() || 'No error message.';
  const cleaned = clean(text);
  return cleaned.length > MAX_ERROR_CHARS ? `${cleaned.slice(0, MAX_ERROR_CHARS)}\n…(truncated)` : cleaned;
}

function render() {
  if (!existsSync(RESULTS_FILE)) {
    return [
      MARKER,
      `### ❌ Playwright e2e did not finish on \`${sha}\``,
      '',
      'No results were written: the job stopped before or while starting the apps (missing secrets,',
      'install, migration, seed, or a dev server that never came up). This is not a failing spec.',
      '',
      `Job log: ${runUrl}`,
    ].join('\n');
  }

  const report = JSON.parse(readFileSync(RESULTS_FILE, 'utf8'));
  const tests = collectTests(report.suites);
  const failed = tests.filter(({ test }) => test.status === 'unexpected');
  const flaky = tests.filter(({ test }) => test.status === 'flaky');
  const passed = tests.filter(({ test }) => test.status === 'expected');
  const counts = `${failed.length} failed, ${passed.length} passed, ${flaky.length} flaky.`;

  if (JOB_STATUS === 'success') {
    return [MARKER, `### ✅ Playwright e2e passed on \`${sha}\``, '', counts].join('\n');
  }

  const lines = [MARKER, `### ❌ Playwright e2e failed on \`${sha}\``, '', counts, ''];
  for (const error of report.errors ?? []) {
    lines.push('**Run error**', '```', clean(error.message ?? String(error)), '```', '');
  }
  for (const { spec, test } of failed) {
    const where = `front/e2e/${spec.file}:${spec.line}`;
    // Titles come from the PR's code too.
    const heading = clean(`[${test.projectName}] ${where} › ${spec.title}`).replace(/[*\n]/g, ' ');
    lines.push(`**${heading}**`, '```', errorText(test), '```', '');
  }
  if (failed.some(({ test }) => test.projectName === 'setup')) {
    lines.push(
      'The sign-in setup failed, so every signed-in spec was skipped. That is the Auth0 test user, its',
      'row in the database (seed_e2e_user) or the Auth0 secrets, not the change under test.',
      '',
    );
  }
  lines.push(
    `Page snapshots at the moment of failure: the \`playwright-failures\` artifact of ${runUrl}`,
  );
  return lines.join('\n');
}

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

async function findComment() {
  for (let page = 1; ; page += 1) {
    const rows = await github(
      `/repos/${GITHUB_REPOSITORY}/issues/${PR_NUMBER}/comments?per_page=100&page=${page}`,
    );
    const hit = rows.find((c) => c.user?.type === 'Bot' && c.body?.includes(MARKER));
    if (hit) return hit;
    if (rows.length < 100) return null;
  }
}

let body = render();
if (body.length > MAX_BODY_CHARS) body = `${body.slice(0, MAX_BODY_CHARS)}\n…(truncated)`;
if (GITHUB_STEP_SUMMARY) appendFileSync(GITHUB_STEP_SUMMARY, `${body}\n`);

// Never fails the job: the tests decide red or green, not the comment.
try {
  const pr = await github(`/repos/${GITHUB_REPOSITORY}/pulls/${PR_NUMBER}`);
  if (pr.head.sha !== HEAD_SHA) {
    console.log(`PR #${PR_NUMBER} moved on to ${pr.head.sha.slice(0, 7)}; that run reports instead.`);
    process.exit(0);
  }
  const existing = await findComment();
  if (existing) {
    await github(`/repos/${GITHUB_REPOSITORY}/issues/comments/${existing.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ body }),
    });
  } else if (JOB_STATUS !== 'success') {
    await github(`/repos/${GITHUB_REPOSITORY}/issues/${PR_NUMBER}/comments`, {
      method: 'POST',
      body: JSON.stringify({ body }),
    });
  }
} catch (err) {
  console.log(`::warning::Could not write the Playwright PR comment: ${err.message}`);
}

# spec-review: patch vs. spec review tool

## Goal
Make it easier to review a patch (Phabricator revision, jj revision, or patch
file) against the spec(s) it implements. Show which code corresponds to which
spec step, and which spec steps correspond to which lines, with color for
category and for gaps. Mappings come from a careful Claude analysis and are
inherently fuzzy and incomplete; the tool must show confidence, not hide it.

## Scope
v1: one patch at a time, read-only, local web app.
Out of scope for v1 (hooks only, see "Extension points"): whole-stack review,
writing comments back to Phabricator, cost optimization for large stacks.

## Findings from real patches (mc_sendgroup WebTransport stack)
- `webspec-index query SPEC#anchor` returns algorithm steps with hierarchical
  numbering (1, 3.2.1) and a content SHA. Step ids are stable join keys.
- Authored patches often carry `// Step 2.2.1: ...` and `// Not in spec: ...`
  comments. These are strong priors and are also verifiable: stale or wrong
  step numbers are review findings.
- Algorithm-style DOM code maps well (most steps). Plumbing (IPDL, parent/child
  actors, WPT `.ini` expectations) mostly maps to "supporting, no step".
  Steps like "in parallel" or "queue a network task" are structural and have
  no direct lines.

## Architecture
Python + FastAPI backend, static TS/JS front end (no heavy framework), JSON
schema (pydantic) as the contract between the analysis and the UI.

Pipeline:
1. Ingest (`ingest/`): phab revision (existing phab tooling), jj revision
   (`jj diff -r`), or patch file/stdin. Normalized to a `Patch`: files, hunks,
   per-line old/new numbers.
2. Spec discovery: Claude pass 1 proposes `SPEC#anchor` candidates from URLs in
   comments, IDL names, commit text. UI lets the user add/remove before
   mapping.
3. Spec fetch: `webspec-index query` per anchor, parsed into `Step` trees.
4. Mapping: `claude -p` headless with `--json-schema`, one call per
   algorithm / file group, given the hunks and step list. Claude may read the
   repo for context. Author `Step N:` / `Not in spec:` comments are passed as
   priors to verify, not trusted.
5. Validation (deterministic): line ranges exist in the diff, step ids exist,
   comment hints vs. mapping disagreements flagged.
6. Cache: on disk, keyed by (patch hash, spec SHA, prompt version).
7. Serve: FastAPI serves results and the UI. User edits are stored as sidecar
   JSON and survive re-analysis.

## Data model
- `Step { spec, anchor, id ("2.2.1"), text, parent, kind: algorithm|note }`
- `Link { step_id, ranges: [{file, side, start, end}], kind, confidence,
  rationale }`; ranges may be non-contiguous; a range may appear in several
  links.
- `kind` (color categories):
  - `implements` green
  - `partial` amber
  - `structural` blue (concurrency/queuing steps, no direct line)
  - `deviates` orange (contradicts or reorders the step)
  - `unimplemented` red (step with no link)
  - `not-in-spec` gray + hatch, with reason `plumbing | error-path | test |
    unexplained`; only `unexplained` is flagged
- `Finding { anchor (step or range), text, severity }`: short review notes
  (e.g. "step 4 unimplemented", "order differs from spec").
- `confidence`: 0..1, shown as opacity/badge; low-confidence filter in UI.

## UI
Three panes: spec (left), diff (right), filter bar (top).
- Spec pane: steps with hierarchical numbering, status color per step.
- Diff pane: colored gutter stripe per line (stripe only, preserves syntax
  readability); multi-step lines get a multi-color stripe; step id labels at
  the right edge.
- Click a step: highlight all its ranges, scroll to the first, next/prev
  buttons jump between non-contiguous ranges. Click/hover a line: highlight its
  steps, popover with rationale and confidence.
- Minimap strips along both panes show coverage (unimplemented steps and
  unexplained code stand out at a glance).
- Filters: unmapped steps, unmapped code, low confidence, deviations,
  per-category toggles.
- Each category also has a pattern/shape; the UI must not rely on color alone.
- Overrides: reassign a link, mark wrong/ok, edit the spec set; saved as
  sidecar JSON.

## Testing
Fixtures from the WebTransport stack: `getStats` (`kmsrpxorkwly`) as the clean
case, `WebTransportWriter` (`pzylxrtqzsop`) as the messy multi-process case.
Deterministic parts get golden-file tests: `Step N:` comment parser, range
validator, coverage computation, diff normalization. The Claude mapping is
tested via recorded responses (schema conformance + spot checks), not live
calls.

## Extension points (described, not implemented in v1)
- Stack support:
  - `Patch` already has `id` and `parent_ids`; v1 always passes a single-node
    list. Analysis takes `list[Patch]`.
  - `Link` carries `patch_id`. Coverage computation is a pure function over a
    set of links, so stack coverage is the same function over the union.
  - UI hook: patch selector in the top bar (hidden when one patch); a step is
    "unimplemented" only if no patch in the set covers it; show which patch
    implements which steps.
  - Cache key per patch, so re-analysis of one patch in a stack is cheap.
  - Cost controls (cheaper summary model first, batching, concurrency limits)
    belong in a `Mapper` interface with a pluggable strategy; v1 has a single
    per-algorithm strategy.
- Write-back: a `Reporter` interface consumes `Finding`s. v1 ships none beyond
  the UI; a Phabricator inline-comment exporter (or markdown export) would
  implement it.
- Other ingest sources: `Ingest` interface returning `Patch`; add GitHub PRs
  or others without touching analysis.
- Other spec sources: `SpecProvider` interface returning `Step` trees; v1 uses
  `webspec-index` only (RFCs and non-web specs could plug in later).
- Server port: because the contract is JSON schema, the server could be
  rewritten (e.g. Rust) without changing the UI or cached results.

## Decisions
- Local web app, three-pane layout (chosen over inline annotation and
  matrix views; matrix could become a stack-overview later).
- Auto-detect specs with user override.
- Author comments are hints to verify.
- `claude -p` headless, not the raw API.
- Python + FastAPI.
- Inputs: Phabricator revision, jj revision, patch file.

## Open questions
None blocking v1.

## Revision 1
- Prose specs (IETF drafts and other anchors without a numbered list): the
  content is split into paragraph items. A paragraph ending in a pilcrow
  link `[¶](...#section-6.3-1)` closes an item whose number is the fragment
  without `section-` and whose `target` is the fragment; preceding
  pilcrow-less paragraphs (field labels, code blocks, figure captions) join
  it, and trailing ones join the last item. Without any pilcrow, paragraphs
  become items `1..n` (a single paragraph stays step `0`). Pilcrows are
  removed, markdown escapes unescaped and links reduced to their text.
- `Step` gains `title` (short outline header, at most about 70 characters),
  `target` (fragment id the rendered spec scrolls to) and `depth` (indent
  level, no longer derived from the dots in `number`). `SpecDoc` gains
  `anchor`. `PROMPT_VERSION` is "2" so older cached analyses are not reused.
- The spec column shows an outline (number and title per step, status color
  and badge, full text in the tooltip) above the live spec page in an iframe,
  with Split / Rendered / Outline view modes. The page is cross-origin, so
  status colors stay on the outline. Selecting a step sets the iframe to
  `base#target` (or the doc anchor); only the fragment changes within one doc,
  so the page does not reload. An "open in new tab" link covers iframe load
  failures.
- Selecting a step scrolls the diff to its largest contiguous implementation
  range (ranges merged per file and side when overlapping or adjacent; ties go
  to the first in diff order); `n` / `p` cycle from there. Clicking a diff line
  selects its first linked step.
- The popover is replaced by a docked, collapsible detail strip at the bottom
  of the diff pane (resizable by its top edge, collapsed until something is
  selected, `Esc` or the button collapses it); it never overlaps code.

## Revision 2
- Manual mode (`--manual` / `--no-ai`): no Claude calls. `AnnotationMapper`
  (`annotations.py`) builds links from `<spec> URL` ... `</spec>` blocks with
  `Step NN: text` comments in the patch's new-side (added and context) lines.
  Each Step covers the following lines up to the next Step or `</spec>`
  (markers excluded). Repeated steps merge into one `implements` link
  (confidence 1.0) with several ranges. `annotation_refs` supplies the specs
  (block URLs in order) unless `--spec` is given.
- A hunk is only a window into the file. Block and step state reset at each
  hunk (lines before the first Step are unattributed), so a `</spec>` with no
  opener or a block still open at the end of a hunk is not an error. A Step
  with no `<spec>` opener in its window resolves its URL from, in order: the
  current block in the hunk; the last `<spec>` URL seen earlier in the same
  file (this also applies after a `</spec>`); the default URL (the single
  `--spec`, else the single distinct URL in the patch). Otherwise a warning
  is emitted and the Step is skipped.
- A step's range is trimmed to its first and last non-blank added lines;
  interior context lines are kept. A step region with no added lines (an
  unchanged step visible as context) produces nothing. "No code" only warns
  when a following Step or `</spec>` ends a region with no non-blank line.
- Diagnostics are findings: warn for an unresolvable spec, unknown step
  number and a Step without code; info for comment text sharing under half
  its words with the spec step, and for `commit-snapshots` URLs
  (webspec-index resolves them to the latest indexed spec, so step numbers
  may differ from the pinned commit).
- `specreview --example` analyzes the bundled
  `src/specreview/examples/manual_example.patch` in manual mode.
- `NullCache` replaces `Cache` in manual mode so deterministic results never
  collide with AI cache entries. `analyze` takes a `no_specs_message` so the
  manual error tells the user to add `<spec>` blocks or pass `--spec`.

## Revision 3
- The `source` argument may be a whole source file. `ingest` detects by
  content: a diff has a line starting with `diff --git ` or matching the hunk
  header regex; anything else is source. Source becomes a `Patch` with one
  `FileDiff` (path as given, `stdin` for `-`) and one hunk
  `@@ -0,0 +1,N @@` of added lines numbered 1..N (`diffparse.whole_file`),
  id = file stem, empty description. Empty files and files containing NUL
  bytes are `IngestError`s, as is a diff with no files.
- No changes to annotation parsing or the UI: the single hunk makes step and
  block state span the file, and `<spec>` URL inference uses earlier blocks in
  the file. `mapper.render_patch` has no size assumptions; there is no
  chunking, so large files give large prompts.
- `--example-file` (implies `--manual`) shows the bundled whole-file example
  `examples/manual_example.cpp`; `--example` still shows the diff example.

## Revision 4
- Right-click compliance check on an outline row: `compliance.py` builds a
  prompt (numbered diff, whole spec text with the entry marked TARGET, the
  entry's mapped ranges, patch source, context guidance) and
  `ComplianceChecker.check` returns a `ComplianceResult` (verdict
  compliant / partial / non-compliant / unclear, summary, issues with optional
  file:line, evidence, `context_used`); `step_id` is stamped by us.
- Context is the AI's job; we only provide tools and say what exists. With
  `--repo`, claude runs in the repo and is told the working copy may lack the
  patch (the patch text is authoritative). Without it, the prompt points at
  `searchfox-cli`. A Phabricator source (`Patch.source` = `D12345`) adds
  stack guidance (raw diffs at `/D<N>?download=true`); a `jj:REV` source with
  a repo adds read-only jj guidance. `Patch.source` is excluded from
  `digest()`.
- Tool list (`COMPLIANCE_TOOLS`): Read, Grep, Glob, Bash(searchfox-cli:*),
  Bash(webspec-index:*), Bash(jj log|show|diff|file show:*) and WebFetch for
  the Phabricator host. `run_claude` takes `tools`. Live testing showed the
  `Bash(cmd:*)` and `WebFetch(domain:...)` forms work in headless mode, but
  `--allowedTools` only pre-approves: with sandbox auto-allow on in the user's
  settings, unlisted Bash commands (even `touch`) still ran. So `run_claude`
  now always passes `--permission-mode dontAsk` and
  `--settings {"sandbox":{"autoAllowBashIfSandboxed":false}}`, after which
  unlisted commands and Edit/Write are denied. Phabricator raw diffs redirect
  to `mozphab-phabhost-cdn.devsvcprod.mozaws.net`, which is allowed too.
- Cache: `Cache.compliance_key/get_compliance/put_compliance` under
  `<root>/compliance/`, keyed by patch digest, doc ref and sha, step id,
  `COMPLIANCE_PROMPT_VERSION` and model (not the repo). `NullCache` never
  stores.
- Server: `ReviewState` gains `checker`, `ai_enabled` and `cached_checks`; the
  payload carries `ai_enabled` and `checks` (cached results for the current
  analysis). `POST /api/check` is a sync endpoint (threadpool): 409 when AI is
  off, 404 for an unknown step, 502 on `ClaudeError` (invalid model output is
  also a `ClaudeError`). The closure in `cli.compliance_hooks` looks the
  step up in the current analysis at call time, so it follows `/api/rerun`.
- UI: custom `#ctxmenu` on outline rows (closes on click, scroll, Escape;
  disabled with a tooltip in manual mode or while that step runs), a bordered
  pill on the row (distinct from the filled mapping badge), and a
  "Compliance (AI)" section in the detail strip with clickable file:line
  entries that scroll the diff. Errors show in the strip, not in an alert.

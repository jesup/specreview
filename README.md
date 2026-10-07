# specreview

A local web app that maps one patch onto the specification algorithm steps it
implements. It shows the spec steps, the diff and the links between them, with
lines and steps colored by mapping category.

## Install

```
uv sync
```

Requires `claude`, `webspec-index` and (for `jj:` inputs) `jj` in `PATH`.

## Usage

```
uv run specreview D12345|jj:REV|file.patch|File.cpp|- [--spec REF ...] [--repo PATH]
                  [--model ALIAS] [--port 8765] [--refresh] [--no-open] [--manual]
uv run specreview --example|--example-file [--port 8765] [--no-open]
```

- `D12345`: a Phabricator revision (public revisions only).
- `jj:REV`: a jj revset, resolved in `--repo` (default: the current directory).
- `file.patch`, `File.cpp` or `-`: a git-style diff or a whole source file,
  from a file or stdin; see "Whole-file input".
- `--spec SPEC#anchor` (or a spec URL): repeatable. Without it, specs are
  discovered from spec URLs in the patch comments, falling back to a Claude
  query. The discovered refs are cached with the analysis (see "Caches and
  overrides"); pass `--spec` to choose them yourself.
- `--repo`: repository used for `jj` and as Claude's working directory for
  reading context.
- `--refresh`: ignore the cached analysis and discovery and replace them with fresh
  ones. The Re-run button in the UI does the same. Compliance checks have
  their own Re-check button.
- `--no-cache`: neither read nor write the cache for this run.
- `--manual` (alias `--no-ai`): build the mapping from `<spec>` annotations in
  the patch, with no Claude calls; see "Manual mode".
- `--example`: no source needed; analyzes the bundled example patch in manual
  mode and opens it in the browser. The spec is fetched with `webspec-index`,
  so the example needs network access.
- `--example-file`: like `--example`, but analyzes the bundled whole source
  file `src/specreview/examples/manual_example.cpp` (implies `--manual`).

The server listens on `127.0.0.1` and opens the browser unless `--no-open`.

## Whole-file input

The source can be an entire source file instead of a diff. Detection is by
content: the text is a diff if any line starts with `diff --git ` or matches a
hunk header (`@@ -N[,M] +N[,M] @@`); otherwise it is source. A source file
becomes one new file (path as given, `stdin` for `-`) with a single hunk
`@@ -0,0 +1,N @@` in which every line is added, numbered 1..N, so everything
shows as added lines. The patch id is the file stem and the description is
empty. Empty and binary (NUL-containing) files are errors. It works in both
AI and manual mode; in manual mode block and step state runs through the whole
file, and a Step without an opener takes the most recent `<spec>` URL earlier
in the file. In AI mode the whole file is sent in the prompt, so very large
files produce very large prompts (about 75 characters per line in the
prompt for typical code); there is no chunking.

## Manual mode

`--manual` (or `--no-ai`) never calls Claude, neither for mapping nor for spec
discovery. The mapping comes only from annotations the author wrote in the
added or context lines of the patch:

```
// <spec> https://foo.spec.whatwg.org/commit-snapshots/COMMIT/#foo
// Step 3.2: Do foo

(... code ...)

// Step 3.3: Do bar
(... code ...)

// </spec>
```

```
uv run specreview D12345 --manual
uv run specreview file.patch --no-ai --spec WEBTRANSPORT#dom-webtransportsendstream-getstats
```

- `<spec> URL` opens a block bound to that spec URL and anchor; `</spec>`
  closes it. Comment prefixes: `//`, `#`, or a leading `*` (also `/*`) inside
  block comments.
- `Step NN: text` marks a step; `NN` is the spec's own step number (as shown
  in the outline, for example `3.2.1` or `6.3-1`). A Step comment covers the
  lines after it up to the next Step comment, the `</spec>`, or the end of the
  hunk. The marker lines are not part of the range, and directly following
  comment lines with the same prefix continue the step text.
- A step's range runs from its first to its last added non-blank line;
  leading and trailing context and blank lines are excluded and interior
  context lines are kept. A step region with no added lines (an unchanged step
  that is only visible as context) produces no link and no warning.
- A step annotated in several places becomes one link with several ranges.
  Links are `implements`, confidence 1.0, rationale "author annotation".
- A hunk is only a window into the file, so blocks and steps may open or close
  outside it: a `</spec>` with no opener, a block still open at the end of a
  hunk, and a hunk that starts in the middle of a step are all fine. Step
  state resets at each hunk (lines before the first Step are unattributed).
- A Step with no `<spec>` opener in its window gets its spec from: the
  current block in the hunk; else the most recent `<spec>` URL earlier in the
  same file (also after a `</spec>`); else the default URL, which is the
  single `--spec` value if exactly one was given, otherwise the single
  distinct URL in the whole patch. If none applies, a warning "cannot
  determine the spec for Step NN" is emitted and the Step is skipped.
- Warning findings: unresolvable spec, a step number missing from the spec, and
  a Step followed by a Step or `</spec>` with no non-blank line. If fewer
  than half of the words in the comment appear in the spec step, an info
  finding says the comment text differs.
- Without `--spec`, the specs are the distinct block URLs in order of
  appearance; `--spec` overrides.
- Added lines outside any step stay "unexplained", and steps never annotated
  stay unimplemented (a step whose substeps are annotated shows as
  structural). No "not in spec" entries are generated.
- Results are deterministic and are never cached.

`uv run specreview --example` shows a bundled patch
(`src/specreview/examples/manual_example.patch`) that exercises these rules:
a full block, a hunk that starts mid-step and ends with `</spec>`, a new step
added inside an existing block with no opener in view, and an unannotated
step that renders as unimplemented. It needs network access, since the spec is
fetched with `webspec-index`.

Limitations:

- `webspec-index` ignores `commit-snapshots/COMMIT/` and resolves to the
  latest indexed spec, so step numbers may differ from the pinned commit; an
  info finding is added for each such URL.
- Only the lines inside the visible hunks (3 lines of diff context) are seen;
  step boundaries outside them are unknown.
- Only annotated lines are explained; nothing verifies that the code does
  what the step says.

## Legend

Steps and diff lines are colored by category:

- green: implements
- yellow: partial
- blue: structural (satisfied by structure, ordering or queuing, not particular lines)
- orange: deviates
- red: unimplemented step (no link in this patch)
- gray: not in spec (plumbing, error-path, test, unexplained)

Keyboard: `n` / `p` jump to the next / previous linked region, `Esc` collapses
the detail strip.

## Layout

- Spec column: an outline (number and short title per step, colored by
  status; the full text is the tooltip) above the live spec page in an iframe.
  The Split / Rendered / Outline buttons switch the view; drag the outline's
  bottom edge to resize it. The page is cross-origin, so it is not colored.
  Use "open in new tab" if the iframe fails to load.
- Clicking an outline row (or a diff line) points the rendered spec at that
  step and scrolls the diff to the step's largest contiguous implementation
  range; `n` / `p` cycle through the other lines.
- Detail strip: docked below the diff, collapsed until something is selected;
  drag its top edge to resize.
- Specs without a numbered algorithm (for example IETF drafts) are split into
  paragraph items numbered by their section anchor (`6.3-1`).

## Compliance check (AI)

Right-click an outline row and pick "Check compliance with AI" (or "Re-check")
to ask Claude whether the code satisfies that one spec entry (a numbered
algorithm step or an IETF paragraph item). The prompt contains the whole patch
(numbered as in the diff view), the full spec text with the entry marked, the
ranges mapped to the entry and the patch source; Claude judges only that
entry and decides how to gather more context. The row shows a "checking..."
pill, then a small bordered pill (ok / partial / fail / ?; dark green, amber,
red, gray) with the summary as its tooltip. Selecting the step shows a
"Compliance (AI)" section in the detail strip: verdict, summary, issues and
evidence (file:line entries that exist in the patch are clickable and scroll
the diff there), the context Claude says it used, and a Re-check button.
Several steps can be checked at the same time. Results are cached per patch,
spec SHA, step and model, and are shown again after a reload; Re-check forces a
new run.

Context and tools, with all of them read-only (`claude -p` runs in `dontAsk`
mode with Bash auto-approval turned off, so anything not listed is denied):

- Allowed: `Read`, `Grep`, `Glob`, `Bash(searchfox-cli:*)`,
  `Bash(webspec-index:*)`, `Bash(jj log|show|diff|file show:*)` and `WebFetch`
  for `phabricator.services.mozilla.com` (and the CDN host its raw diffs
  redirect to).
- With `--repo` (or a `jj:REV` source with `--repo`), Claude runs with the
  repository as its working directory. The prompt says the working copy may
  not contain the patch's changes: the patch text is authoritative for changed
  code and the repository is for surrounding code, callers, headers and IDL.
- Without a repository, Claude is told to use `searchfox-cli` for code already
  merged upstream (searchfox does not contain the patch).
- For a Phabricator source (`D12345`) Claude may look at the other revisions
  of the stack and fetch their diffs (`/D<N>?download=true`); for `jj:REV` with
  `--repo` it may use `jj log`, `jj show`, `jj diff` and `jj file show`.
- Not available with `--manual`: the menu item is disabled and the server
  answers 409.

Each check is a full Claude run with tool use, so it can take a minute or
more and costs real tokens; use `--model` to pick a cheaper model. A verdict
of "unclear" means the available context could not decide.

## Caches and overrides

Under `~/.cache/specreview` (or `$XDG_CACHE_HOME/specreview`):

- `analysis/<key>.json`: the analysis, keyed by the patch digest, prompt
  version, model and the spec content SHAs. The patch digest is a SHA-256 of
  the patch contents (every file, hunk and line, plus the commit message), so
  any change to the source invalidates the entry; so does a changed spec
  (the SHA `webspec-index` reports; run `webspec-index update` to refresh its
  index), a new prompt version or a different `--model`. The UI shows
  whether the loaded result is "cached" or "fresh" with its timestamp.
- `discovery/<key>.json`: the spec refs found for a patch when no `--spec`
  was given, keyed by the patch digest, discovery prompt version and model.
  Empty results are not cached.
- `compliance/<key>.json`: AI compliance results, keyed by the patch digest,
  spec ref and SHA, step id, compliance prompt version and model.
- `overrides/<digest>.json`: your per-link "ok" / "wrong" verdicts.

## How mapping works

The patch is normalized into files, hunks and per-line old/new numbers. The
spec anchors are fetched with `webspec-index` and parsed into a step tree
(numbered algorithm steps, or paragraph items for prose). Claude is given the steps, the numbered diff, the commit message and the
`Step N` / `Not in spec` claims from the author's comments, and returns links
from steps to line ranges, unmapped ranges with a reason, and findings. The
result is validated (ranges must exist in the diff, step ids must exist, comment
step numbers are checked against the mapping) and violations become warnings.

## Extension points

- Stack support: `Patch` carries `id` and `parent_ids` (always a single node
  in v1). `Link.patch_id` records which patch a link belongs to, and the
  cache is keyed per patch.
- `Mapper` protocol in `mapper.py`: the strategy that turns patch, spec docs
  and hints into links; cost controls (cheaper models, batching,
  concurrency) belong here.
- `coverage.py` is a pure function over a set of links, so stack coverage is
  the same function over the union of the patches' links.
- `ingest.load` dispatches on the source string; add other sources (for
  example GitHub PRs) there, returning a `Patch`.
- `spec.fetch_spec` returns a `SpecDoc` of `Step` trees; other spec providers
  can plug in there.
- The pydantic JSON schema is the contract between analysis and UI, so the
  server could be rewritten without changing the UI or cached results.

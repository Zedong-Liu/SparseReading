# Independent Codex/Pi v0.1.2 review

Date: 2026-09-28  
Base candidate: `39cdf80`; focused follow-up commit: `45ee453`  
Scope: host error/evidence contracts, session reset and fallback, record-output privacy, and release packaging.

## Conclusion

No confirmed P0/P1 adapter or release-code blocker found. The original P2 replay-metric finding is resolved in `45ee453`. The P3 broken-relative-link finding is fixed in the committed guide, but the review document itself is not yet in that commit; include this report in the final tagged source so its versioned link resolves. The hidden-checksum mismatch is resolved. No tag or published release assets were created or reviewed here; release CI should still validate the exact final tagged commit.

## Findings

### [P2 — resolved in `45ee453`] Native route previously implied an unobserved native read

Original finding: the `decision == "native"` branch set
`native_source_pass=True` without calling a host-native read, so the Codex JSONL
case overstated what that per-case flag established.

Follow-up: the field is now `native_source_oracle_pass`, and the code comment
states that it checks the local source oracle, not a host-native read. The
regression test asserts the old field is absent. In the regenerated aggregates,
the native case reports `evidence_pass` and `raw_pass` as `null`; the oracle flag
does not claim native execution. Pi's separate `native_tool_executes` and
`jsonl_native_executes` checks cover actual native calls. I independently ran
the focused replay test. No native-read or token-saving claim is inferred from
the oracle flag.

### [P3 — link fix committed; report inclusion pending] Source installer omits linked review documents

Original finding: the source allowlist includes
`docs/codex-pi-adapters.md` but omits `docs/codex-pi-real-records.md` and
`docs/codex-pi-v012-review.md`, leaving the guide's former relative links broken
in an extracted bundle.

Follow-up: in committed `45ee453`, the guide now uses absolute, version-pinned
GitHub links. The linked documents remain online references rather than offline
bundle contents. I checked the committed tree: the real-records report is
present, but this review report is still untracked and therefore absent from
`45ee453`. Include this report in the final tagged commit, then verify the
release archive and that both tag URLs resolve. This is a documentation/release
content condition, not an install or P0/P1 blocker.

## Resolved checksum issue

`write_sha256sums` now excludes hidden files, matching the release upload glob
`dist/*` and preventing `dist/.gitignore` from being listed without being
uploaded. The regression fixture creates a hidden `.gitignore`; after the fix,
`uv run --project . --extra test pytest -q tests/test_release_bundle.py` passed
**21 tests**. Correction to my earlier report: the owner reports **18 hashed
content artifacts** plus the `SHA256SUMS` attachment, for **19 release
attachments**; my earlier wording incorrectly called these 19 digest checks.
The owner reports all 18 content-asset digests match the upload set. I did not
independently rebuild the final `dist/`.

## Independently verified

- Full Python suite on the candidate before the checksum-only patch:
  **254 passed**. The release-bundle suite above was rerun after that patch.
- Pi package tests on Node 24.15.0 with the supplied
  `SPARSEREAD_PI_RUNTIME_FILE`: **19 passed, 0 skipped**. The installed-runtime
  Pi SDK load and managed-bridge preview/raw smoke ran and passed.
- Focused follow-up on the code now committed as `45ee453`:
  `uv run --project . --extra test pytest -q tests/test_record_replay.py
  tests/test_release_bundle.py` — **25 passed**.
- The committed v0.1.2 metadata validator passed. Raw-range descriptions use
  zero-based Unicode character offsets with an exclusive end; the regression
  suite passed. Host error tests and the replay aggregates show stale references
  surface as failures, not evidence.
- The new `tool_calls` and `serialized_response_chars` metrics are bounded to
  each replay case. The latter sums Python character lengths of compact JSON
  serializations of returned tool-response objects; it is not a byte or token
  count and excludes transport framing. Erroring calls abort the case rather
  than being represented as successful responses. Compact JSON overhead is
  included: I confirmed serialized responses exceed source-character counts for
  some short cases. That is reported overhead, not a token-saving claim.
- The regenerated replay JSON contains only short scenario IDs, routes,
  booleans, sizes/counts/ratios/timings, and check metrics. It contains no paths,
  goals, needles, expected terms, response bodies, raw references, transcripts,
  or credentials. New metrics expose counts only, not response text. I did not
  emit or copy record bodies and did not touch or approve personal Pi
  configuration.

## Owner-reported, not independently rerun

The main agent reports that clean ZIP/tar source bundles installed both hosts
and passed both doctors; cold Codex and Pi record replays passed; remote CI on
`2f520c3` passed its three jobs including Windows and Pi on Node 22; the final
full Python suite passed **254 tests**; and installed-runtime Pi tests passed
**19 tests with no skips** on Node 22/24. The owner also reports 18 content
artifact digests matching, with the checksum manifest as the 19th attachment.
These owner-run checks are not counted as independent evidence above. The final
tagged commit still needs the release-workflow check, and must include this
review file for its versioned link to resolve.

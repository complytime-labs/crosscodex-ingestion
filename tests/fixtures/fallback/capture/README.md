# Golden capture tool

`main.go` runs crosscodex's document structurer on one ingestion output document and writes the
result as `{"tier": ..., "items": [...]}`: the oracle for the Python fallback structurer. This
directory is not built or tested by this repository; the program is copied into a crosscodex checkout.

## Regenerate the goldens

```sh
task fixtures:fallback-goldens CROSSCODEX_DIR=/path/to/crosscodex
```

`CROSSCODEX_DIR` is any crosscodex git checkout that has commit `80f764fe` (Go, `task` and `buf` must be
installed). The task adds a temporary worktree at that commit under `/tmp`, copies `main.go` into its
gitignored `.test-output/golden-capture/`, runs `task generate` there, then for each
`tests/fixtures/fallback/*/input.json` runs `go run ./.test-output/golden-capture <input> <golden>`. It
removes the worktree afterwards, also on failure. Your checkout's files are not changed.

## Behaviour

- Options are crosscodex's `catalog.structuring` defaults: decomposition on, 40 minimum words, at most 3
  heading repeats, no keyword filter, no section pattern, no LLM.
- Items are Go's `json.Marshal` of `oscal.ControlItem` (`ID, Title, Text, Class, ParentID, GroupID,
  ContainerKey, Props`).
- The golden is `{"tier": "none", "items": []}` when the cascade ends in `ErrStructureFailed` or the
  `fallback` (paragraph) tier, which the port does not have, and for an input with no elements, which the
  Go adapter rejects before the cascade runs.
- The program panics on input that carries element roles: the cascade does not run on those.

## Recapture the real layouts

`capture_layout.py` writes the layout the converter produces for a source document. It is runnable only at
ingestion commit `d68ecb0`: later commits emit roles, not layout elements. See `../PROVENANCE.md`.

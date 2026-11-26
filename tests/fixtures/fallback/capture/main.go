// Command golden-capture records the output of crosscodex's document structurer
// cascade (heading, table, pattern tiers, then decomposition) for one ingestion
// output document, as golden data for the crosscodex-ingestion fallback port.
//
// It is not part of any module: `task fixtures:fallback-goldens` copies it into a
// temporary crosscodex worktree at the pinned commit and runs it there.
//
// Usage: go run ./.test-output/golden-capture <input.json> <golden.json>
//
// The options are crosscodex's catalog.structuring defaults. The golden is
// {"tier": ..., "items": [...]}; a document the cascade cannot structure
// (ErrStructureFailed) or only the paragraph fallback tier would handle gets
// {"tier": "none", "items": []}, because the port has no such tier. An input with
// no elements, which the adapter rejects before the cascade, gets the same golden.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"

	"github.com/complytime-labs/crosscodex/internal/catalog"
	"github.com/complytime-labs/crosscodex/pkg/oscal"
)

func main() {
	if len(os.Args) != 3 {
		fmt.Fprintf(os.Stderr, "usage: %s <input.json> <golden.json>\n", os.Args[0])
		os.Exit(2)
	}
	data, err := os.ReadFile(os.Args[1])
	if err != nil {
		panic(err)
	}
	var shape struct {
		Elements []json.RawMessage `json:"elements"`
	}
	if err := json.Unmarshal(data, &shape); err != nil {
		panic(err)
	}
	if len(shape.Elements) == 0 {
		// The adapter rejects an output with no elements before the cascade runs;
		// the port's equivalent of that rejection is "no structure".
		writeGolden(os.Args[2], map[string]any{"tier": "none", "items": []oscal.ControlItem{}})
		return
	}
	doc, meta, err := catalog.AdaptIngestionOutput(data)
	if err != nil {
		panic(err)
	}
	if meta.SourceItems != nil {
		panic("input carries roles; the cascade does not run on it")
	}
	opts := oscal.StructureOptions{Decompose: true, MinDecomposeWords: 40, ChunkChars: 3000, MaxValidationChars: 800, MaxHeadingRepeats: 3}
	items, tier, err := oscal.NewStructurer(nil, nil).StructureWithTier(context.Background(), doc, opts)
	golden := map[string]any{"tier": string(tier), "items": items}
	switch {
	case errors.Is(err, oscal.ErrStructureFailed), err == nil && tier == oscal.StructureTierFallback:
		golden = map[string]any{"tier": "none", "items": []oscal.ControlItem{}}
	case err != nil:
		panic(err)
	}
	writeGolden(os.Args[2], golden)
}

func writeGolden(path string, golden map[string]any) {
	out, err := json.MarshalIndent(golden, "", "  ")
	if err != nil {
		panic(err)
	}
	if err := os.WriteFile(path, append(out, '\n'), 0o644); err != nil {
		panic(err)
	}
}

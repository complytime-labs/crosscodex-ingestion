#!/usr/bin/env python3
"""Download the Docling and RapidOCR models into a directory, pinned by revision.

Usage: python scripts/download_models.py <output_dir>

`docling-tools models download` cannot pin revisions: it has no revision option, and
Docling's own specs request the moving `main` ref for the layout, code/formula and
picture-classifier repositories. Pinning the docling package in uv.lock therefore does not fix
the model contents. This script fetches each Hugging Face repository at the full commit SHA in
PINNED_HF_MODELS into the directory name Docling expects (`<org>--<name>`), so an image
rebuild yields the same weights.

RapidOCR checkpoints are not on the Hugging Face Hub; they are fetched through Docling from
URLs fixed by the installed Docling/RapidOCR versions, so they are pinned by those package
versions rather than by this table.

To bump a model: resolve the new commit SHA (`HfApi().model_info(repo, revision=...).sha`),
review the upstream change, update the table. After a Docling upgrade, the offline container
test (`task test:container`) fails if the default PDF pipeline (layout, TableFormer, OCR) now
needs a repository missing from this table; it does not exercise the code/formula or
picture-classifier models, which that pipeline leaves disabled.
"""

import sys
from pathlib import Path

from docling.utils.model_downloader import download_models
from huggingface_hub import snapshot_download

# repo_id -> full commit SHA. Repositories and revisions Docling 2.148 requests
# (revision "main" unless noted): layout (PyTorch + ONNX engines), TableFormer (tag v2.3.0),
# code/formula, picture classifier.
PINNED_HF_MODELS = {
    "docling-project/docling-layout-heron": "8f39ad3c0b4c58e9c2d2c84a38465abf757272d8",
    "docling-project/docling-layout-heron-onnx": "40bde044036bb181c130ddf6c51792187268748f",
    "docling-project/docling-models": "fc0f2d45e2218ea24bce5045f58a389aed16dc23",
    "docling-project/CodeFormulaV2": "ecedbe111d15c2dc60bfd4a823cbe80127b58af4",
    "docling-project/DocumentFigureClassifier-v2.5": "f859dfbff5c9916cd996942d4b0db7fa25808220",
}


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(f"Usage: {sys.argv[0]} <output_dir>")
    output_dir = Path(sys.argv[1])
    output_dir.mkdir(parents=True, exist_ok=True)

    for repo_id, revision in PINNED_HF_MODELS.items():
        snapshot_download(repo_id=repo_id, revision=revision, local_dir=output_dir / repo_id.replace("/", "--"))

    # RapidOCR only: every Hugging Face model above is already present at its pinned revision.
    download_models(
        output_dir,
        with_layout=False,
        with_tableformer=False,
        with_code_formula=False,
        with_picture_classifier=False,
        with_rapidocr=True,
    )


if __name__ == "__main__":
    main()

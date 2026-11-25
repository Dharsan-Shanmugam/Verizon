"""Shared helpers: tests needing the CSVs / trained artifacts are skipped when absent."""
from vdi import data_loader as dl
from vdi import pipeline

HAS_DATA = not dl.check_data()
HAS_ARTIFACTS = HAS_DATA and pipeline.artifacts_ready()
SKIP_DATA = "CSV data not found (set VDI_DATA_DIR)"
SKIP_ART = "data or trained artifacts missing (run python -m vdi.pipeline train)"

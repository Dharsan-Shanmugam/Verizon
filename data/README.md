# Data folder

The approved synthetic CSVs are included directly in this repository. A fresh
clone needs no separate ZIP or data download:

```
data/
├── 00_data_dictionary.csv
├── 00_data_provenance.csv
├── 01_venues.csv
├── ...
└── 36_service_quality_15s_current_event.csv
```

The data totals approximately 396 MB before Git compression. The loader also
supports the legacy `data/Verizon data/` layout.

To use another location, set `VDI_DATA_DIR`:

```bash
export VDI_DATA_DIR="/path/to/Verizon data"        # Mac / Linux
$env:VDI_DATA_DIR = "C:\path\to\Verizon data"       # Windows PowerShell
set VDI_DATA_DIR=C:\path\to\Verizon data             # Windows cmd
```

All supplied files are synthetic (see `00_data_provenance.csv`). The column
`true_occupancy_sim_only` (files 14 and 15) is simulator ground truth and is never used as a model feature.

The **38th CSV**, `36_service_quality_15s_current_event.csv`, contains
populated synthetic RTT/loss and TDD/RF scenarios. It is derived from the supplied
data without overwriting it and regenerated when its scenario version changes.
See [service-quality provenance](SERVICE-QUALITY.md). Do not replace these files
with real customer or network data in a public repository.

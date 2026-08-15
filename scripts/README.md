# Reproducible scripts

This directory contains selected scripts used to compute the published repository statistics and figures. Paths to the binary dataset are intentionally not hard-coded for distribution.

Set the event dataset root before running the statistics scripts:

```bash
export TLCD_DATASET_ROOT=/path/to/Dataset
python scripts/stat_dataset_events.py
python scripts/stat_scene_diversity.py
```

The trajectory figure uses the pre-segmentation road-test records and accepts their two city roots explicitly:

```bash
python scripts/figures/plot_city_trajectories.py \
  --nanjing-root /path/to/Nanjing \
  --changchun-root /path/to/Changchun
```

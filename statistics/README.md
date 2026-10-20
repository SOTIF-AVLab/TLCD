# Statistics and quality-control summaries

These files describe the validated TLCD 1.0 event inventory used by the manuscript figures and repository overview.

The composition tables retain the current manuscript's reporting basis: 5,173 events, including 3,455 compliant events and 1,718 violations. They are manuscript-analysis outputs, not a new recount of all event files after subsequent annotation revisions. The road-test mileage and duration follow the manuscript table reproduced in [`DATASET_INFO.md`](../DATASET_INFO.md#collection-boundary).

- `dataset_event_statistics.md`: event composition by city, category, provision, driving mode, and compliance label.
- `scene_diversity_statistics.md`: primary event subtypes and non-exclusive contextual attributes.
- `qa_summary.json`: machine-readable inventory and completeness checks.
- `01_city_article.csv`–`04_category_combination.csv`: compact tables used to generate the dataset-composition figure.
- `scene_diversity/`: compact subtype and contextual-label tables used to reproduce the scene-diversity figure.

Statistics count each valid `record.json` as one event record. When an event applies to multiple legal provisions, provision-level tables count one event–provision pair per applicable provision.

# Architecture decision records

Why the project is built the way it is. Each record states the context (with measurements where there are any), the decision,
and the consequences, including the costs. To add one, copy [0000-template.md](0000-template.md), number it next, and link it
from where the decision shows up.

| # | Decision |
|---|---|
| [0001](0001-local-first-processing.md) | Process everything locally |
| [0002](0002-motion-segments-and-onset-anchored-frames.md) | Read only the moving frames, anchored on motion onset |
| [0003](0003-fixed-camera-deskew-and-two-detector-sizes.md) | Level the plates for a fixed camera, keep an un-rotated fallback |
| [0004](0004-paddleocr-cpu-python-312.md) | PaddleOCR on CPU, on Python 3.12 |
| [0005](0005-two-pass-reading-and-agreement.md) | Read every saved plate twice, use agreement as the signal |
| [0006](0006-plate-shape-hint-not-a-character-filter.md) | Plate shape (2:1) as a hint, never filter by letters or digits |
| [0007](0007-human-review-and-append-only-labels.md) | Local review page, append-only labels |
| [0008](0008-auto-accept-queue-and-weak-labels.md) | Let confident, agreeing, plate-shaped reads skip the queue |
| [0009](0009-data-layout-and-share-caching.md) | Results beside the camera folder, text files, cache for a slow share |
| [0010](0010-polling-watcher-and-ntfy-alerts.md) | Polling watcher, LaunchAgent, ntfy alerts, one-character fuzzy matching |
| [0011](0011-review-queue-requires-second-pass.md) | A plate is reviewable only after its second pass |
| [0012](0012-confusion-rerank-deferred.md) | Defer confusion-aware reranking |
| [0013](0013-config-file-and-single-cli.md) | One TOML config, one `lpr` command |

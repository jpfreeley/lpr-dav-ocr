# Contributing

Thanks for looking. This is a small project with a few strong rules, mostly about privacy.

## The one rule that matters most

**Never commit real data.** License plates are personal data. No recordings, frames, crops, indexes, labels,
watchlists, notification topics, camera identifiers, host names, user names or file paths from your own setup.
Tests, documentation and screenshots use synthetic data from `lpr demo` (plates like `DEMO001`).

Before every push:

```bash
scripts/check-private-data.sh
```

It checks the files git tracks. CI runs it too. Git history is permanent, so catching it before the push is the only
good outcome. See [docs/privacy.md](docs/privacy.md).

## Setup

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[ocr,dev]"
python -m unittest            # no OCR models needed
ruff check .
```

The OCR extras are only needed to run the real pipeline. Most tests run with just `numpy` installed.

## Making a change

1. Branch from `main`. Do not commit directly to `main`.
2. Keep the change focused. Add or update tests. The server, watcher, scoring and helpers have tests in `tests/`.
3. If it changes behaviour a user relies on, update the docs and the [changelog](CHANGELOG.md).
4. If it changes a design decision, add an [ADR](docs/adr/) (copy `0000-template.md`) rather than editing an accepted one.
   Supersede it with a new record.
5. Run the tests, `ruff check .` and `scripts/check-private-data.sh`.

## Conventions

- Plain, direct language in docs and comments. Explain why, not what.
- No hardcoded paths, hosts or identifiers. Configuration goes through `lpr_dav_ocr.config`.
- Close your files. The watcher is a long-running process (`lpr_dav_ocr.util` has helpers).
- Do not filter plate reads by letters or digits. Vanity and numbers-only plates are valid.
- Processes that load PaddlePaddle end with `_finish()` (see [KI-3](docs/known-issues.md#ki-3)).
- Anything that sends data off the machine must be opt-in and documented in `docs/privacy.md`.

## Reporting a problem

Open an issue with what you ran, what you expected and what happened. **Do not attach frames, labels or logs that
contain plates.** Redact them or reproduce the problem with `lpr demo` data.

# ADR 0001: Process everything locally

- Status: Accepted
- Date: 2026-10-22

## Context

The input is footage of people's vehicles and the output is a timestamped list of license plates. That is
personal data. Cloud OCR services are accurate and easy, but they would send frames of other people's plates to a
third party, on every clip, indefinitely.

## Decision

All processing, review, scoring and storage run on the user's own machine. The only network use is:

1. a one-time download of the OCR models (`lpr setup`), and
2. optional push alerts through an ntfy server the user chooses.

At run time the program sets offline flags (`HF_HUB_OFFLINE`, `TRANSFORMERS_OFFLINE`, model-source checks off) so
a missing model fails loudly instead of silently downloading. The review server binds `127.0.0.1` only and loads no
external scripts or fonts.

## Consequences

- Throughput is bounded by a laptop CPU. A full day of clips takes hours (see [usage](../usage.md)).
- Model files must be cached before first use.
- Alerts are the one path where data leaves: see [ADR 0010](0010-polling-watcher-and-ntfy-alerts.md) and
  [privacy](../privacy.md).
- No account, key or quota to manage.

## Alternatives considered

- **Hosted ALPR/OCR APIs.** Rejected for the privacy reason above.
- **A cloud GPU for training later.** Not adopted. Fine-tuning, if done, stays local ([ADR 0007](0007-human-review-and-append-only-labels.md)).

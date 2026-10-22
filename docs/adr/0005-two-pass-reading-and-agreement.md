# ADR 0005: Read every saved plate twice and use agreement as the signal

- Status: Accepted
- Date: 2026-10-22

## Context

First-pass accuracy was 85.9% exact match. Confidence alone was not enough: one confidently wrong read (0.99 to
1.00) was a look-alike character, and clipped reads scored high.

## Decision

After the pipeline saves a frame, `lpr second-pass` re-reads it independently: no rotation, a 960 px detector, and no
video decoding. It records its own read and confidence, the text box, and the shape of the text region.
A plate enters the human review queue only once its second pass exists ([ADR 0011](0011-review-queue-requires-second-pass.md)).

## Consequences

On 342 labelled plates with two reads:

- Passes agree on 87%, and then 95.6% are right.
- Passes disagree on 13%. The first pass is right 29.5% of the time, the second 43.2%, and neither 27.3%.
- Taking the higher-confidence read gives 89.5%. Either-right is 92.7% (the ceiling for any combination).

So agreement is a cheap, strong reliability signal, and disagreement is where a human is worth most. The cost is a
second read of every saved frame (about 2 seconds each) and a dependency between steps. The second pass also supplies the
plate's location, which the review page needs.

## Alternatives considered

- **A second OCR engine as the second opinion.** More diverse errors, more dependencies. Not tried beyond the
  comparison in ADR 0004.
- **Majority vote across many frames.** Partly done inside a burst (variants are grouped, the best read wins) but
  per-frame reads are not independent enough to replace a different configuration.

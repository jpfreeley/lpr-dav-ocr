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

## Addendum: ladder and crop read

At about 1,000 labels, with two days of data, first-pass exact accuracy was 78% (88% at night, 74% by day) and the
higher-confidence pick of two reads was 82%. Where the two passes disagree (144 labeled cases), neither was right in
50 and one was right in 94.

- **Ladder.** The second pass tries un-rotated 960 px, then rotated 960 px, then rotated 640 px. The last rung gives an
  outline only and leaves `plate2` blank, so it cannot manufacture agreement. This removed most frames with no outline.
- **Crop read.** On disagreement only, a third read of a tight crop (15% horizontal and 50% vertical padding, enlarged to
  200 px tall, 640 px detector, un-rotated) breaks the tie: use it when it equals one of the two reads, otherwise keep the
  higher-confidence read. 76 of 144 right against 69 for the old rule; 70 of 131 against 63 after being filled on live
  data. Contrast equalization (CLAHE) made reads worse. A logistic model over confidence, hour, shape and length gained
  about two reads in cross-validation and was not adopted.
- The confusion-table rerank (ADR 0012) was re-tested leave-one-out at 1,029 labels: 81.5% against 82.3%. It stays off.

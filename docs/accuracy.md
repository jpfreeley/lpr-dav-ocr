# Accuracy

What was measured, how, and what it does and does not mean. These are **aggregate numbers from the author's
own camera and labels**. They show what the method can do, not what it will do on your footage. Re-measure
with `lpr score`.

## How it is measured

A person reviews each plate the pipeline found ([review-ui.md](review-ui.md)). A verdict of *correct* means the
first-pass read was right. *Save fix* means the typed correction is the truth. Only those two verdicts have a
known plate, so only they are scored.

**What the numbers cannot say.** Every image scored is one where the pipeline found a plate-shaped read.
So these figures are the **precision of reads**: when the pipeline reports a plate, how often is it right? They say
nothing about plates it never reported (recall). Measuring recall needs a separate, exhaustive labelling of a
sample of clips.

## Results from one labelled day (479 reviewed, 354 with a known plate)

| Read | Exact-match accuracy |
|---|---|
| First pass (rotated 20 degrees, 640 px detector), all known | 85.9% |
| On the 342 that also have a second read: first pass | 87.1% |
| Second pass (no rotation, 960 px) | 88.9% |
| Take the higher-confidence of the two | 89.5% |
| Either pass right (upper bound for any combination) | 92.7% |

Rotation against no rotation is **not a clear win**: the second pass leads by 6 plates in 342, and it also
differs in detector size, so the difference cannot be credited to rotation alone.

### Agreement is the most useful signal

| Situation | Count | Outcome |
|---|---|---|
| Passes agree | 298 | right 95.6% |
| Passes disagree | 44 | first pass right 29.5%, second pass right 43.2%, neither right 27.3% |

### Confidence means something

| First-pass confidence | Right |
|---|---|
| below 0.90 | 17.6% |
| 0.90 to 0.97 | 55.3% |
| 0.97 to 0.99 | 79.3% |
| 0.99 and above | 98.7% (66% of plates) |

### Where errors fall

Character error rate 3.2%. By position: middle 45, first 23, last 13. The largest confusion by far is **H read for M**
(12 of 81 character errors), then 0 for D, 1 for T (a leading T read as 1), 0 for 8, 7 for 1, M for W, N for M.
The full table is `reports/confusions.csv` after `lpr score`.

### Plate shape (the 2:1 aspect idea)

The aspect ratio of the detected text region: real plates 1.94, 2.19, 2.41 (10th, 50th, 90th percentile);
things that were not plates (model names, state text, signs) median 3.35 with a 90th percentile of 5.46. A
shape other than `ok` flagged 36 of 57 non-plates and **0 of 342** real plates. It is used as a ranking hint and
an auto-accept condition. Some non-plate text is plate-shaped, so it is not a complete filter.

### The auto-accept rule

Confidence at or above 0.99, both passes agree, shape ok. It covered about 45% to 49% of plates. Of the 236 labelled
plates matching it, the real plates were right 222 of 223 times (99.6%); 13 were not plates or unreadable, so the
rule saves review effort but does not remove non-plates. **One day of labels from one camera is thin evidence.**
Audit the rule with the "Auto-accepted: spot check" view and `lpr score` before relying on it.

### Confusion-aware reranking (tested, not adopted)

Choosing between the two reads with per-pass character confusion tables, evaluated leave-one-out: 90.4% against
89.5% for the higher-confidence read. That is 3 plates in 342, within noise. See
[ADR 0012](adr/0012-confusion-rerank-deferred.md). Re-test with about 1,000 labels.

## Day against night

| | First pass |
|---|---|
| Night (00:00 to 07:00) | 87.4% |
| Day | 84.8% |

On a 22-clip daylight sample, the night settings did not carry over unchanged. Plate tilt varied from about -5 to
20 degrees (against a steady 20 at night), and motion from foliage and people produced up to 37 bursts in one clip.
Limiting bursts, and retrying empty ones without rotation, ran the sample in 266 s against 1300 to 2300 s and found
more real plates. See [ADR 0003](adr/0003-fixed-camera-deskew-and-two-detector-sizes.md).

## Reproduce and extend

```bash
lpr score                  # writes <data_root>/reports/accuracy.md and confusions.csv
```

More labels make every number above firmer. A reasonable plan: score again at about 300 labels (rerank), and at about
1,000 (consider fine-tuning the recogniser, see [ADR 0007](adr/0007-human-review-and-append-only-labels.md)).

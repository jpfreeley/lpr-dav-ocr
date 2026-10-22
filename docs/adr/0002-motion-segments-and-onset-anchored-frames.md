# ADR 0002: Read only the moving frames, anchored on motion onset

- Status: Accepted
- Date: 2026-10-22

## Context

A motion-triggered clip is about 20 seconds (600 frames at 30 fps) and a plate is legible for under a second.
Reading every frame would cost hours per day. The recording starts before the trigger and continues after the vehicle
leaves, so most of a clip is an empty pre- and post-record buffer. An early idea was that the plate would sit near the
centre of the clip.

Measured on the author's camera (8 plates across 7 clips): the best plate frame was at 11% to 33% of the clip
(2 to 7 seconds in), and 0.1 to 0.7 seconds after motion onset. Onset itself ranged from frame 54 to frame 206 of
about 570. The clip's centre is therefore a poor guess, and a fixed window misses plates.

## Decision

1. Decode each clip once at low resolution and compute a per-frame motion score (mean absolute change from the
   previous frame, outside the burned-in overlay boxes).
2. Threshold it at `max(min_motion, median + k * MAD)` and group moving frames into bursts. A burst is roughly one
   vehicle pass.
3. Rank bursts by motion mass and keep the strongest `max_segments`. Read every `stride`th frame of each, capped at
   `max_frames`, with a few frames of padding.
4. Fetch those frames by **frame index**, not by time. (ffmpeg seeks by time land on the wrong second in these
   files because their timestamps start at a large epoch offset.)
5. Stop a burst early once enough confident reads agree (`stop_conf`, `stop_hits`, with a `min_frames` floor).

## Consequences

- Typical cost falls from about 600 frames to under 20 OCR calls per clip.
- Multiple vehicles in one clip are separate bursts, and a plate is saved for each.
- In busy daylight clips, foliage and people create many bursts. Capping them bounds the cost but can miss a real
  vehicle ([KI-9](../known-issues.md#ki-9)).
- File size does not indicate vehicle count: the bitrate was steady at about 0.8 MB/s, so size tracks duration only.
- The `min_frames` floor exists because a first confident read was sometimes a clipped plate.

## Alternatives considered

- **Read the centre of the clip.** Rejected by the measurements above.
- **Read every Nth frame regardless of motion.** About 30 times the OCR work for the same result.
- **A trained vehicle or plate detector.** Heavier and needs training data that did not exist yet. Reconsider once labels do.

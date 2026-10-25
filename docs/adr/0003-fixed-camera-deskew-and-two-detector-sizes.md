# ADR 0003: Level the plates for a fixed camera, and keep an un-rotated fallback

- Status: Accepted
- Date: 2026-10-22

## Context

The camera is fixed, so plates share a tilt. At night on the author's camera the plate text sloped about 20 degrees
(measured 19 to 21.5 on six plates). The text detector runs on a downscaled image: at the native 1080p it took about
8 seconds per frame, at 960 px about 2.2 s, at 640 px about 1.1 s. But at 640 px, un-rotated frames lost plates
(5 of 7 found), because the tilted text got too small to detect.

In daylight the tilt was not constant (about -5 to 20 degrees depending on where the vehicle passed), and rotation
alone lost 2 of about 16 real plates in a 22-clip test.

## Decision

- Rotate frames counter-clockwise by `camera.tilt_degrees` (20 by default) before OCR and run the detector at
  `pipeline.det_side` = 640. Rotated, 7 of 7 plates were found at 640 px, about 1.5 times faster than 960 px.
- Blank the burned-in overlays before rotating, so the timestamp is never read as a plate.
- Where rotation finds nothing in one of the strongest bursts, retry a few frames **un-rotated at 960 px**
  (`fallback_segments`, `fallback_frames`).
- A second pass over saved frames also uses un-rotated 960 px ([ADR 0005](0005-two-pass-reading-and-agreement.md)),
  so every plate has one read from each configuration.
- Tilt is a configuration value to be measured per camera ([configuration](../configuration.md#measuring-the-tilt)).

## Consequences

- About 2x faster per clip than un-rotated 960 px, with similar hit rate.
- A wrong tilt value silently costs plates. Measure it, and re-measure if the camera moves.
- Daylight tilt variation is only mitigated by the fallback ([KI-8](../known-issues.md#ki-8)).
- Tested on one site. The numbers will differ on another.

## Alternatives considered

- **Per-frame tilt estimation** (from the detected text angle) then rotate. Better in principle, more code, not done.
- **Run both rotations on every frame.** Doubles the cost.
- **Lower resolution without rotation.** Loses plates, as measured.

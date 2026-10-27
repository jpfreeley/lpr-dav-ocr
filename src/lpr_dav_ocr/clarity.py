"""How clear is a saved plate frame? A score from 0 to 100, used to suggest the gold standard frame of a plate.

Nothing here marks anything: it ranks the sightings you verified so the clearest can be offered to you. Five things count,
each turned into a percentile rank among all frames measured so far (so the scale adapts to your camera and light):

  sharpness   variance of the Laplacian inside the plate box (log scale)         30%
  size        height of the plate box in pixels (more pixels, more detail)        20%
  contrast    the spread of brightness inside the box (5th to 95th percentile)    10%
  exposure    little of the box clipped to pure black or pure white               10%
  reads       first-pass confidence, both passes agreeing, the crop read agreeing  20%
  shape       how close the box is to a face-on 2:1 plate                         10%

The expensive part (reading the frame) happens once per frame and is cached in <home>/clarity.json.
"""
import bisect
import math

WEIGHTS = dict(sharp=0.30, height=0.20, contrast=0.10, exposure=0.10, reads=0.20, shape=0.10)
FACE_ON = 2.1                       # a plate seen straight on is about twice as wide as it is tall


def frame_stats(path, box):
    """{sharp, h, w, contrast, clip} for the plate box of one frame, or None when the frame or box is unusable."""
    import cv2
    import numpy as np
    if not box:
        return None
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None
    x0, y0, x1, y1 = (int(v) for v in box)
    crop = img[max(0, y0):max(0, y1), max(0, x0):max(0, x1)]
    if crop.shape[0] < 6 or crop.shape[1] < 6:
        return None
    lo, hi = np.percentile(crop, [5, 95])
    clip = float(((crop <= 3) | (crop >= 252)).mean())          # share of pixels blown out to white or crushed to black
    return dict(sharp=float(cv2.Laplacian(crop, cv2.CV_64F).var()), h=int(crop.shape[0]), w=int(crop.shape[1]), contrast=float(hi - lo), clip=clip)


class Population:
    """Sorted measurements of every frame seen so far, to turn a value into a percentile rank (0 to 1)."""

    def __init__(self, stats):
        self.sharp = sorted(math.log10(1 + s["sharp"]) for s in stats)
        self.height = sorted(s["h"] for s in stats)
        self.contrast = sorted(s["contrast"] for s in stats)

    @staticmethod
    def _rank(sorted_values, v):
        if len(sorted_values) < 2:
            return 0.5
        return bisect.bisect_left(sorted_values, v) / (len(sorted_values) - 1)

    def ranks(self, s):
        return dict(sharp=self._rank(self.sharp, math.log10(1 + s["sharp"])), height=self._rank(self.height, s["h"]),
                    contrast=self._rank(self.contrast, s["contrast"]))


def reads_score(conf, agree, crop_agrees):
    """0 to 1 from the reader's own certainty: confidence from 0.8 up, both passes agreeing, the crop read agreeing."""
    return 0.5 * min(max((conf - 0.8) / 0.2, 0.0), 1.0) + 0.35 * bool(agree) + 0.15 * bool(crop_agrees)


def exposure_score(clip):
    """1 when nothing is clipped, 0 when a third or more of the box is pure black or white."""
    return 1.0 - min((clip or 0.0) / 0.33, 1.0)


def shape_score(aspect):
    return 1.0 - min(abs((aspect or FACE_ON) - FACE_ON) / 1.0, 1.0)


def score(stats, conf, agree, crop_agrees, aspect, pop):
    """Clarity 0 to 100 of one verified sighting, or None when the frame could not be measured."""
    if not stats:
        return None
    r = pop.ranks(stats)
    total = (WEIGHTS["sharp"] * r["sharp"] + WEIGHTS["height"] * r["height"] + WEIGHTS["contrast"] * r["contrast"]
             + WEIGHTS["exposure"] * exposure_score(stats.get("clip")) + WEIGHTS["reads"] * reads_score(conf, agree, crop_agrees) + WEIGHTS["shape"] * shape_score(aspect))
    return round(100 * total)

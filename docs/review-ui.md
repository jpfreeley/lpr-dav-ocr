# The review page

`lpr review` starts a small web server on `127.0.0.1` (default port 8770). Nothing is sent anywhere. The
page is for labelling what the pipeline found, which gives you accuracy numbers and training data.

![Review page](images/review-ui-demo.png)

## What is on the page

- **Left:** the saved frame with the plate outlined, the clip and approximate time, and navigation: Previous,
  Next, **Back** (the plate you were just looking at, like a browser back button, one step at a time) and **Next unreviewed**.
- **Top right:** a leveled crop of the plate. The letters of the current text are drawn under it, one per plate
  position, so you can compare read against plate. Letters you changed turn amber.
- **OCR read:** the read and its confidence, a plate-shape warning when the text region is not plate-shaped,
  an *auto-accepted* badge when the rule applies, and **2nd pass says X**: the second read. Click it to copy
  that read into the boxes.
- **Character boxes:** one input per character. Typing replaces the character and moves on. Arrow keys move
  between boxes. `+ box` and `- box` insert and delete a box. A changed box shows what it was ("was H"), an
  inserted one shows "new". The comparison is an alignment, so adding a character at the front does not mark
  every later box.
- **State:** `UNKN` (default), common states, and a free-text two-letter box. It is stored with the verdict.
  Nothing resolves it automatically.
- **Buttons:** see below.
- **Watch:** adds the plate (the corrected text if you edited the boxes) to the watchlist. See
  [watcher-and-alerts.md](watcher-and-alerts.md).

## A review session, step by step

This is what reviewing a plate feels like. Most plates need one or two clicks.

1. **Look.** The frame shows the plate outlined, and the leveled crop sits beside it with the current text drawn under it,
   one letter per position. Most of the time the read is right and you press **Correct** (or `Enter`).
2. **Read the hints.** When the read is doubtful, the page puts clickable offers around the character boxes. Each one is
   a suggestion: nothing changes until you click it.
   - **2nd pass says X**, the crop read and the **best guess**: other readings of the same frame. Click one to copy it into the boxes.
   - **Pattern badges** (for example "fits NYS standard", or the NYC taxi pattern) with a **set NY** or **set NJ** button.
   - **Same last four**: known plates with the same digits and one or two different letters, most-seen first.
   - **One character from a plate you verified**: "one character from ABC1234 (verified 3x within a month) - use".
   - **Seen N times in the last 7, 30 or 90 days**: click the number for a montage of every earlier sighting.
   - **Watch**: shows if the plate is on your watchlist, and adds it with one click.
3. **See the vehicle.** If the plate has a record in the local NYC ticket index, one line shows the make, body type, year
   and color (for example "2020 BMW SUV or wagon, blue"). If you can see that the car does not match, press **doesn't
   match** to mark the record verified stale. When no state chip is picked yet, the record also picks the state for you.
4. **Fix by typing.** Change a box and everything above follows your text (next section). A changed box shows what it
   was ("was H").
5. **Save.** **Correct** or **Save fix** stores your label, applies the state to every other sighting of that plate, and
   moves to the next plate. Later sightings of a plate you verified are approved for you.

## Hints and ticket data follow your edits

The hints, the "seen N times" count, the watch status and the vehicle line are not computed once when the plate opens.
They are recomputed from the text in the boxes every time you edit them. Type `ABC1234` where the read said `A8C1234` and
the vehicle line, the state chip, the history count, the pattern badge and the offers all switch to the plate you typed.
A line beside the count states what it is checking ("checking ABC1234: fits the NYS standard pattern, you verified it 3
other times nearby"). Clear a box or press **Reset** and they return to the OCR read.

Because everything is local, this is effectively instant. The check starts 250 ms after your last keystroke, so it does
not run on every key. The lookup itself, covering the vehicle record, the same-last-four and verified-plate hints, the
watchlist and the history count, took about 1 to 2 ms per request on the author's Mac (the largest index is 650 MB of
SQLite, queried by plate). The first request after starting the server can take about half a second while caches warm.
Nothing leaves the machine: the NYC ticket index is a local file (see [usage](usage.md#vehicle-make-body-year-and-color)).

## Verdicts

| Button | Meaning | Training use |
|---|---|---|
| **Correct** (Enter) | The read is right. Enabled only while the boxes are unchanged. | positive example |
| **Save fix** (Enter) | You changed at least one box. The boxes are the truth. | corrected example |
| **Not a plate** (Alt+N) | There is no plate in the outlined area (a sign, a model name, a dealer frame). | false-positive example |
| **Obstructed** (Alt+O) | A plate is there but cannot be read (blocked, glare, blur, cut off). | excluded from accuracy, useful for quality filtering |
| **Can't tell** (Alt+S) | You are unsure what it is. | excluded |

Enter does the right thing for the state of the boxes: no change means Correct, a change means Save fix.
After each verdict the page moves to the next plate that needs review.

## Which plates are queued

The header menu chooses the view. Counts are per day. A **day** menu lists every processed day.

| View | Shows |
|---|---|
| **Needs review** (default) | Plates with a second pass, no verdict yet, and not auto-accepted. |
| **Auto-accepted: spot check** | Plates that met the auto-accept rule. Look at some to audit it. |
| **All unreviewed** | The first view plus the auto-accepted ones. |
| **Everything** | Every plate, including ones still waiting for a second pass. |

A plate enters a review queue only after the second pass has run on it, even when that pass found no read.
Plates still waiting are counted in the header ("waiting for 2nd pass") and join the queue on their own.

**Auto-accept rule:** confidence at or above `review.auto_accept_conf` (0.99), both passes agree, and the
text region has a plate-like shape. Auto-accepted plates get **no** human label, so treat them as weak labels.
Validate the rule on your own data before trusting it: `lpr score` prints its precision.

## Keyboard

| Key | Action |
|---|---|
| Enter | Correct, or Save fix when a box was changed |
| Alt+N / Alt+O / Alt+S | Not a plate / Obstructed / Can't tell |
| Alt+Left, Alt+Right | Previous, next |
| Alt+B (or Alt+L), Alt+U | Back, next unreviewed |
| Left, Right, Backspace | Move between character boxes |

Plain letters are not shortcuts because they would clash with typing into the boxes.

## Where your labels go

`<data_root>/labels/labels.jsonl`, one JSON object per line, appended and flushed immediately. The latest line
for an image wins, so you can revisit and relabel. Fields:

| Field | Meaning |
|---|---|
| `ts` | when you saved it (ISO 8601 with offset) |
| `image`, `clip`, `day` | which plate |
| `plate_ocr`, `conf` | the first-pass read and its confidence |
| `plate_second`, `shape` | the second-pass read, the shape class at that time |
| `verdict` | `ok`, `fix`, `notplate`, `obstructed` or `skip` |
| `truth` | the true plate for `ok` and `fix`, else null |
| `state` | `UNKN` or a state code |

For `ok` and `fix`, a leveled crop is saved to `<data_root>/crops/<image name>__<truth>.png`.

Labels are keyed by the image file name. Do not rename saved frames.

## NYC TLC pattern hint

Taxi and for-hire plates in New York City are a T or Y, six digits and a letter (for example `T123456C`). On the
author's labels the reader got 24 of 71 such plates wrong, almost always reading the T as `1` or the final C as `0`.
When a read can be made to fit the pattern, the page offers **NYC TLC pattern T123456C - use** and one click copies it
into the boxes. If the read already fits, a small "fits the NYC TLC pattern" badge appears instead.

It is a hint only. It never filters plates, never changes a stored read and never feeds auto-accept. On the author's
labels the rule would have fixed 16 of the 24 wrong TLC reads and made 3 wrong suggestions out of 19, so check it
against the frame. The pattern is local to one city's regulations: change `tlc_fix` in `review/server.py` for your own area.

## Auto-reject: reads that are not plate-shaped

A read whose text region is much wider than a plate (length over `review.reject_aspect` times the height, default 3.5) is
auto-rejected. US plates are about 2:1 and real plate reads cluster at 1.8 to 2.4; wide regions are usually a bumper
sticker, a dealer frame or a model name. On the author's labels 120 of 120 reads above 3.5 were not usable plates (118
"not a plate", 2 obstructed) and none was a real plate. The rule catches about 38% of all "not a plate" labels; the rest
have normal shapes.

- The plate leaves the **Needs review** queue and the unreviewed view. The **Auto-rejected: spot check** view lists
  them, and a red badge says why. Label one **Correct** or **Save fix** and it is a real plate again.
- No label is written (a weak label, like auto-accept). Rejected reads are left out of plate history and never trigger
  a watchlist alert.
- A plate that equals one you verified recently is auto-approved instead, so a vouched plate is never rejected.
- **A frame is kept in review when the second pass also found plate-shaped text in it** (aspect 1.5 to 3.0, a different
  text from the wide read). The usual cause is a model badge or decal read with high confidence while the real plate
  sits elsewhere in the frame. The page then shows "plate-shaped text elsewhere in the frame XYZ 0.876 - use" so you can
  copy that read into the boxes. On the author's labels 13 of the 16 real plates above 3.5 were such frames (the first
  read was `SPORT`, `4MATIC`, `NEWYORK` and the like), and the other 3 were correct plate reads with a wide shape. The
  second pass looks for this only when its chosen read is wide, so it costs nothing on normal frames. Existing days are
  filled in with `lpr second-pass <folder> --fill-elsewhere`.
  The second pass also stores where that text is (`elsewhere_box`). Clicking the hint moves the blue outline and the
  zoomed crop to it, and Reset puts them back. When you saved that text as the plate, the history, montage and gold crops
  use the same box. `--fill-elsewhere` also adds the box to older rows that have the text but no box.
- `lpr score` prints how many labeled reads were above the threshold (not counting frames kept for a plate-shaped read elsewhere) and how many were real plates. A threshold of 3.0
  also removes 3 real plates in 162, so the default stays at 3.5. Set `review.reject_aspect = 0` to switch it off.

## Gold standard frames

Some plates in the history are fuzzy but legible, and some frames are perfectly clear: there is no question what the read is.
Mark such a frame as the **gold standard** of its plate. A plate has one at a time (a new mark replaces the old). It is saved in `labels/gold.jsonl`.

- **Mark it:** where you look over all the sightings of a plate: the star on any sighting in the **montage** (click the "seen N
  other times" number on the review page, which also lists the sighting under review) or in the plate history. Auto-accepted
  sightings are often the clearest, so every sighting has a star, whether you verified it or not. Starring a frame you have
  not verified also saves its read as verified (Correct), because you are saying there is no question what it says. A frame you
  marked not a plate, obstructed or can't tell cannot be one. For an especially clear frame you are reviewing, the **gold
  standard** button beside **History** and **Watch** does the same: on a frame you have not saved yet it reads "gold when
  saved", and pressing **Correct** or **Save fix** then marks it. A filled star means it is the gold standard; click again to
  remove it.
- **Compare them at full scale:** in the montage, click any sighting to open a viewer over it. It shows the plate crop
  enlarged (the **F** key switches to the whole frame with the plate box drawn on it), steps through the sightings with the
  Left and Right arrow keys, shows each one's time, basis, confidence and clarity, and has the star right there (**S**). Esc
  closes the viewer, and a second Esc closes the montage. **Frame image, full size** opens the saved frame at its native pixels in a
  new tab, and the arrow link on each montage cell (or **O** in the viewer, or Ctrl-click on a cell) opens the review page for
  that plate in a new tab.
- **See it:** the plate history shows the gold frame at the top, and the montage always shows it first (outlined in amber, even
  when it is outside the day window). On the review page, a reading of that plate (or of a plate one character away, such as
  from a hint) shows a "★ gold standard in this plate's history" line with the gold crop beside the vehicle line, so you can
  compare a fuzzy read with the clear one.
- **Suggested:** for a plate with two or more verified or auto-accepted sightings and no gold frame yet, the history page and the montage point
  at the clearest verified sighting ("suggested", dashed amber outline) and show each one's **clarity score** from 0 to 100.
  One click on the star, or on **Make it the gold standard**, accepts it. Nothing is ever marked for you. The score combines
  sharpness (variance of the Laplacian in the plate box, 30%), plate size in pixels (20%), how certain the reader was and whether
  both passes agreed (20%), contrast (10%), exposure, meaning little of the box clipped to pure white or black (10%), and how
  face-on the plate is (10%). Each is a percentile rank among all frames measured so far, so the scale follows your camera
  and light. Frames are measured once and cached in `clarity.json` in the local state folder; a plate with many sightings
  may say "measuring N more frames" the first time. On the author's data the top pick was the frame a person would choose for 5
  of 6 plates checked by eye. As you mark real gold standards, the Operations page shows how often the suggestion matched
  your pick, so you can see whether to trust it.
- **Compared:** an unreviewed read that is one character from a plate with a gold standard frame (a substitution, a missing or an extra character), where no read of the frame says the gold plate, is flagged as a possible misread. The review page shows the differing character in red, the gold crop and a **use** button, and the most valuable queue adds the reason. It only flags. It never changes a read or a label for you, and it is a text comparison: a different vehicle with a similar plate is flagged too.
- **Protected:** the duplicate merge always keeps the gold frame, so it is never moved to the trash and deleted.
- **Measured:** `lpr score` reports how the first pass does on your gold frames, a clean test of the reader on clear plates.

## Known non-plate words (state banners, company names)

Some text is printed on plates or vehicles but is never the plate: on New York plates the banner reads `NEWYORK`, Texas plates read `TEXAS`, and delivery trucks carry `FEDEX` and `FEDEX.COM`. On the author's
data every one of the 20 labeled `NEWYORK` reads was wrong (5 not a plate, 12 fixed to the real plate, 3 obstructed), and
the reader gives it with high confidence. `review.not_plate_texts` (default `NEWYORK` and its clipped misreads `NEWYOR`, `NEWYORE`, `EWYORK`, `EWYOR` and `WYORK`, plus `TEXAS`, `FEDEX` and `FEDEXCOM`) lists exact words like this:

- A read that is exactly one of these words is **never auto-approved**, even with high confidence and both passes agreeing.
- It is **auto-rejected**, unless the second pass found plate-shaped text elsewhere in the same frame. Then it stays in
  review, and the **best guess** is that other text ("the first read is the state banner, not a plate").
- Only the exact word counts (`NEWYORKS` is not matched), and it is a list you set, not a rule about letters, so ordinary
  plates are never filtered by their characters. Add other words you see, for example the name of a state or a slogan.

## "Same last four" hints

For a seven-character read whose last four characters are digits (the AAANNNN shape), the page lists known plates that
share those four digits and differ in one or two of the leading letters: "same last 4 as a plate seen before: ABC1234 3x".
Click one to copy it into the boxes; if you set a state on that plate before, the state chip is set too. Known means plates you verified plus auto-accepted ones, and a plate only counts
if it was seen at least once on another sighting, so a plate never suggests itself. They are ranked by fewest differing
letters, then by how often the plate was seen, and at most three are shown. The motivation is a common error: the digits
read well but one or two letters do not.

On the author's labels, 165 corrected AAANNNN plates had the right last four and wrong letters. For 113 of them a known
plate within two letters existed, and it was the right one 107 times (95%), usually the only candidate. It needs the
vehicle to have been seen before, so it helps with repeat visitors and does nothing for a first sighting. About 6% of
reads that were already right also get an offer, which is why it is a button and never automatic.

## Recently verified plates

Once you have verified a plate (Correct, or Save fix with your corrected text), later sightings of it need less work:

- **Same plate:** a read that is identical to a plate you verified within `review.recent_days` (default 30, either side
  of the sighting) is **auto-approved**. It leaves the queue like any auto-accepted plate and gets no label. The badge
  says which plate vouched for it, and the `auto` view lists it so you can spot-check.
- **One character off:** a read that differs by one wrong, missing or extra character (5 or more characters) from such a
  plate gets a one-click offer: "one character from ABC1234 (verified 3x within a month) - use". Nothing changes until you
  click.

Only plates you verified by hand count. Auto-approved plates never vouch for other plates, so an error cannot spread.
On the author's labels (two days) an exact match was right 165 of 168 times (98.2%), and the one-character offers were
the right plate 42 of 65 times (65%) while 16 of those reads were already right, which is why that one is an offer and
not automatic. `lpr score` prints both numbers for your own data. Set `review.recent_days = 0` to switch this off.

## Plate patterns and the state chip

The page knows five common patterns: a NYC taxi (TLC) plate, T or Y, six digits and a letter; a NYS standard plate, three
letters and four digits; a NJ plate, a letter, two digits and three letters; a NY commercial plate, five digits and
two letters; and a NYS moped plate, three digits, two letters and a digit. A read that fits one shows a small badge. Each badge points at a state, so it also has a
**set NY** or **set NJ** button that selects the state chip for you, and using the TLC correction ("NYC TLC pattern
T123456C - use") selects **NY** automatically. These are habits of the plates, not rules: a pattern never rejects or
changes a plate, and it never picks a state on its own except through that TLC button.

## The ticket record as evidence

A read that exists in the local NYC ticket data is very likely right, because a misread rarely lands on another registered
plate. On the author's labels 77% of the correct first reads had a ticket record, against 7% of the wrong ones. A read with a
record was right 93.7% of the time, and a read without one only 24.9%. Two things use that:

- **Auto-accept with a record.** When both passes agree, the shape is plate-like, the confidence is at least
  `review.record_accept_conf` (default 0.97, below the usual 0.99) and the plate has a vehicle in the ticket data, the plate is
  auto-accepted. The badge says "both passes agree and the plate has a vehicle in the ticket data". On the author's labels
  this was right 98.0% of the time (384 of 392; `lpr score` prints it, and the self-check watches it). A record you marked
  **doesn't match** (verified stale) does not count. Set the key to 0 to turn the rule off.
- **You confirm the vehicle.** If the ticket data says Subaru and the car in the frame is that Subaru with that plate, press
  **matches** beside the vehicle line. That is independent evidence the plate is read right, stronger than the text alone.
  The plate then vouches for its other sightings, exact text only, for `review.confirmed_days` (default 365) either side
  instead of 30. **undo** removes the confirmation. A confirmation applies to the record you saw: if a refresh changes the
  record, it no longer counts, as with a stale mark. **matches** only records the vehicle; you still press **Correct** or
  **Save fix** for the plate itself.

## Digits in letter positions

In a standard plate (three letters then four digits) the first three characters are letters, so a digit there is a
misread. When no read fits the pattern but one would after a fix, the page offers it as a button: "three letters then four
digits DAB1234 - use". The fix replaces `0` with `D`, `6` with `G` and `2` with `Z`. I measured on the author's labels
in two ways (all reads with a digit in a letter position, and only the reads where nothing else fit the pattern, which is
when the offer appears): a `0` was really `D` 78 to 81% of the time, a `6` a `G` 84 to 94% and a `2` a `Z` 81 to 93%. An `8`
(53 to 75%) or a `5` (50 to 68%) was close to a coin flip in the offered cases and a `1` had no clear winner (J 29%,
W 20%), so those are left alone. As a whole plate the offered read was right 50% of the time (33 of 65), against 14% for
the raw reads it replaces. It is only an offer, never applied, because 8 of the plates you marked correct do start with a
digit.

## Pattern fit and "best guess"

Many New York plates are three letters then four digits (`ABC1234`), and TLC plates follow the pattern above. When the
two passes disagree, the page offers a **best guess** chosen from the first pass, the second pass and the crop read:
a read that fits one of those patterns wins over one that does not, then a crop read that backs a pass, then the more
confident pass. On the author's 144 labeled disagreements this was right 81 times, against 76 for the crop read alone and
69 for "higher confidence". A plate that already fits the standard pattern shows a small "fits" badge.

Like the TLC hint this is a tie-breaker, not a filter: plates that fit neither pattern (vanity plates, older styles,
other states, 20% of the author's labels) are never rejected or changed. Fixing the characters themselves was tried and
dropped: forcing a read into AAANNNN (for example a `0` in a letter position becomes `D`) was right for 15 of 30
suggestions and rewrote 3 reads that were already right. The common errors (H read as M, N as M) are letter-for-letter,
which a pattern cannot see.

## A plate's state follows the plate

A state you set when you save a **Correct** or **Save fix** is also the state of that plate on all its other sightings that
have no state of their own. Nothing is written to those sightings' labels: the page records one line per plate in
`<data_root>/labels/plate_states.jsonl` (latest line wins), and every view looks it up. So auto-accepted sightings show
the state in History and in the Reports, and the toast after saving says how many other sightings got it ("state NJ
also set on 3 other sightings of ..."). A sighting you labeled with its own state keeps it. If one plate really exists
in two states, the last one you set is used for the sightings without their own state. For plates labeled before this
existed, the state you used most often on that plate is used.

## Review by value

When you cannot keep up with every plate, review the ones that matter most. Choose **Most valuable first** in the queue
menu. It lists the top 40 unreviewed plates across all days (`review.value_queue`), each plate once (a label on it approves its other sightings), ranked by how much one label changes
your results. Reviewing about 20 a day from the top keeps the history clean. The badge above the buttons says why a plate
is there ("Worth a label: ...").

| Signal | Why it ranks high |
|---|---|
| **Watchlist match** (exact, then one character off) | an alert depends on the right plate |
| **One label clears N more** | N other unreviewed reads of exactly the same text lie within 30 days, so one verification approves them all ([Recently verified plates](#recently-verified-plates)) |
| **One character from a plate you verified** | one click on the offer settles it |
| **Repeat plate, the two reads disagree** | a plate that comes back often is worth getting right |
| **Seen on N days** | regulars carry more weight than one-off passers |

A plate with no reason to be early (a one-off pass that nothing else depends on) is left out. When no plate has a reason, the
queue says so and points you to **Needs review**. A label removes the plate from the queue at once, and the sightings it approves drop out on the next refresh. The queue
is only an ordering: it never labels anything, and the normal **Needs review** queue is unchanged. Auto-approved and
auto-rejected plates are not in it, and neither are plates still waiting for the second pass.

## Re-checking "not a plate" labels

The **Re-check** view lists plates you marked **Not a plate** where the second pass found different, plate-shaped text
(aspect 1.5 to 3.0). The usual cause is a first read of a model badge, a decal or a sign while a real plate sits
elsewhere in the frame. On the author's data 59 of 335 "not a plate" labels qualified, about 18%. Each shows an amber
note with the text the second pass found. The "best guess" and "2nd pass" buttons offer it; use **Save fix** to
record the real plate, or **Obstructed** or **Can't tell** if it cannot be read. **Not a plate** again confirms the
label and takes it off the list (it is saved with `rechecked: true`). **Correct** is disabled here because the
first read is the non-plate text. A relabeled plate becomes a normal sighting in history, alerts and scoring.

## Editing updates the checks

As soon as you change a box (after a 250 ms pause, once every box is filled) the page re-checks the text you typed: the
"seen N other times" count and its montage, the same-last-4 hints, the one-character-from-a-verified-plate offer, the
vehicle line from the ticket data (and the state chip, when none is picked), and the Watch button all follow the edited text. A line beside the count says what the typed plate is: "checking ABC1234:
fits the NYS standard pattern, you verified it 3 other times nearby, on your watchlist as ...". If the text matches
a plate you know, the count goes up and the hints disappear; if it is a new plate you will see "not seen again". This
is a confirmation that the edit is right, not a rule. Clear a box or press Reset to go back to the OCR read.

## Camera time

Times on the review page, the montage, the history page and in alerts come from the clip file names, which is the
camera's own clock. They are labeled **CamTime** because that clock can be wrong: on the author's camera it runs one hour
behind the computer, so "06:16 CamTime" was 07:16 by the computer clock. A sighting near midnight can fall on the wrong
date by the same amount.

## Plate history

Next to the Watch button the review page shows **seen N other times within [7 days | 30 days | 90 days | all time]**.
The window is measured either side of the plate on screen and counts the other sightings, including unreviewed reads of the same text. Click the number to open a
montage of those sightings, oldest first; click one to open it. The **History** link opens `/history?q=<plate>`, a
search page with a summary (first and last seen, days, state, sightings by hour of day) and a table of every sighting.

Matching is exact. A plate counts when you confirmed it ("Correct"), corrected it ("Save fix", using your corrected
text), or the auto-accept rule passed it. Plates marked not a plate, obstructed or can't tell never count. Tick
"include unreviewed reads" to also search plates nobody has looked at yet; each row shows its basis
(`verified`, `fixed`, `auto` or `unverified`) so you can tell them apart. The search box suggests plates that contain
what you typed, with counts.

The plate name at the top of a plate's history is a link to [How's My Driving NY](https://howsmydrivingny.nyc/), a public
site that looks up NYC parking and camera violations by plate. Clicking it copies the plate to the clipboard and opens the
site in a new tab; paste the plate into its search box (the toast also names the state when you set one). The site has no
link that opens a given plate, so the plate is not put in the address. The link is sent without a referrer, so the page
address (which contains the plate) does not go to that site either. Nothing about the plate leaves your computer until you
paste it there yourself.

Watching: a watched plate shows **Watching as <nickname>** with a **Stop watching** button in its summary, and a plate
you are not watching has a **Watch** button with an optional nickname. The suggestion chips carry a star (filled when
watched; click to watch with a nickname, or to stop). With no search, the page lists the plates you are watching.
Watching here is the same watchlist as the Watch button on the queue page, and alerts need the watcher
([watcher-and-alerts.md](watcher-and-alerts.md)).

From the command line: `lpr seen ABC1234 --days 30 [--unverified]`.

Sightings are read from the same files as the queue, so a plate appears as soon as it is processed. The first
search after a start can take a few seconds on a slow share.

## Safety

- The server binds `127.0.0.1` only.
- State-changing requests need an `X-Requested-With` header, which a web page on another site cannot add
  to a cross-origin request.
- Image requests are restricted to the day's frames folder.
- There is no login. Anyone with access to your account on the machine can open the page.

## Notes for slow shares

Reads of the index files over a busy network share can take seconds. The server answers from its last good
read and refreshes in the background, and it keeps a local copy of frames it has served in
`<home>/imgcache`. See [ADR 0009](adr/0009-data-layout-and-share-caching.md).

The header links to the [reports](reports.md) page and to the [operations](operations.md) page.

# Reports

`http://localhost:8770/reports` (or the **Reports** link on the review and history pages) shows what your sightings add
up to. Everything is computed on your computer from the frames, indexes and labels in the data root. Nothing is sent
anywhere, and the page loads no outside scripts or fonts.

## What is counted

The same confirmed sightings as the [history page](review-ui.md): plates you marked Correct or fixed, and auto-accepted
ones, including plates approved because you verified them earlier. Plates marked not a plate, obstructed or can't tell,
and auto-rejected reads, are left out. Tick **include unreviewed reads** to count everything that is plate-shaped.
The day chips narrow every chart and number to the selected days. "Returning" is always judged against the whole history,
so filtering to one day does not make every plate look new.

All hours and times are **CamTime**: the camera's clock, taken from the clip file names.

## The reports

| Section | Chart | What it answers |
|---|---|---|
| Overview | five tiles | sightings, distinct plates, share of returning sightings, regulars, share handled without you |
| Traffic | heatmap of hour by day; average by hour | when the camera is busy, and which days differ |
| Visitors | new and returning per day; days each plate was seen; arrival time of regulars; time between repeat sightings; day-to-day overlap | how many vehicles come back, when regulars arrive, and how a day's plates carry into the next |
| Plate mix | plate type by day; state by sighting | standard versus taxi (TLC) versus other plates, and the states you recorded |
| Vehicles | fleet mix (makes, body types, colors, model-year bands); vehicle kind by hour; regulars by vehicle kind; record coverage by plate type | what the traffic is made of, using the local NYC ticket index (`lpr vehicles import`). Taxi is told by the TLC plate pattern. Shown only when the index exists. Records can be old or wrong, so coverage is shown next to the mix |
| Watched | one dot per sighting of each watchlist entry | when a watched plate passed. Rows show the nickname only |
| Regulars | table | plates on 2 or more days, their usual first arrival, last sighting and vehicle (from the ticket record, when there is one). "Steady" means 3 or more days with the first sighting inside a 90-minute window |
| Review effort | what happened to each read, per day; first-pass accuracy by hour | how much work is left, how much was automatic, and where the first read is weakest |

Every chart has a **Table** button that shows the same numbers as a table. Hover or tab to a bar, cell or dot for a tooltip.

## Keeping it current

The numbers are recomputed from the data each time the page asks, so nothing goes stale on the server (it caches the list
of sightings for 20 seconds, and a label you save clears that cache). The page asks when it opens, when you change a day
chip or **include unreviewed reads**, and when you press **Refresh**. Tick **auto-refresh (60 s)** and it asks again every
minute while the tab is visible, keeping your filters and table views and leaving the charts in place until the new
numbers arrive. A hidden tab does not refresh and catches up when you return to it. The choice is remembered in the
browser. The line beside the filters shows when the numbers were last updated, by your computer's clock.

## Privacy

A table of regular plates with their usual times is a record of people's routines. Plate text in the Regulars table is
hidden until you tick **show plate text**, the watched chart uses nicknames, and the charts are aggregate. Treat
screenshots like the data they come from ([privacy.md](privacy.md)). The page binds to `127.0.0.1` only, like the rest of
the review server.

## For developers

`review/reports.py` builds one JSON document (`GET /api/report?days=YYYY-MM-DD,...&unverified=0|1`) from a review `Store`.
It has no HTTP or file access of its own, so it is unit tested with synthetic days. `review/reports.html` draws the charts as inline SVG:
colors are CSS variables with light and dark values, magnitudes use one blue ramp, categories use at most five fixed
hues, and the page is a single file with no dependencies.

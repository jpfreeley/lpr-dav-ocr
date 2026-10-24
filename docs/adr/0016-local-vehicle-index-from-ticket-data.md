# ADR 0016: A local vehicle index from public parking-ticket data

- Status: Accepted
- Date: 2026-10-24

## Context

Knowing the make, body type, year and color that belong to a plate helps review (does a read fit a car of that
kind?) and reports. No free plate-to-vehicle service exists: the paid APIs reject non-consumer use, and the consumer
"add a vehicle" features of parts stores cannot be scripted. New York City publishes parking tickets as open data, and
each ticket carries the plate, state, make, body type, year and color as the ticket writer typed them.

## Decision

Download aggregate rows from the city's open data API, one grouped query per two-character plate prefix, for the last
three fiscal years and the states NY, NJ, PA, CT, MA and FL. Store them in a local SQLite file (about 650 MB). Look a plate
up locally, taking the most common record. The refresh is weekly and builds staging rows that replace the live ones in one
transaction, so a failed download changes nothing.

Use the record on the review page (the state chip is picked from it when none is chosen, unless the record is marked
stale), on the history page, and in the reports. The user can mark a record **verified stale** when they see a vehicle that
does not match. The mark is kept in `labels/vehicle_flags.jsonl` with the record they saw, and nothing else changes.

## Consequences

- No plate leaves the machine to look up a vehicle. Only prefix-level aggregate queries go out.
- Only vehicles ticketed in the city appear, so coverage is partial (about 75% of plates on the author's data, lower for
  out-of-state and commercial plates), and records can be old or wrong. The UI says so.
- The city records no model or trim.

## Alternatives considered

- **Paid plate lookup APIs.** Send every plate to a third party and reject non-consumer use.
- **Query the city API per plate.** Slow, and it reveals each plate to the API.
- **Shrink the index to NY only.** The user declined: out-of-state records are useful.

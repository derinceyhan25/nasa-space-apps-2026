# Fire Calendar - harmonized MODIS + VIIRS burning-activity calendar

**Live site: https://derinceyhan25.github.io/nasa-space-apps-2026/**
(globe + time-lapse; full calendar page: `/calendar.html`)

NASA Space Apps Challenge 2026 - challenge **"Harmonization of MODIS and VIIRS Hot Spots"**.
Team "derin" (solo), local event Poznan, Poland.

## What it does

Satellites have detected active fires since 2000, but the record is split between two
sensors that cannot be compared directly: MODIS (1 km pixels, since 2000) and VIIRS
(375 m pixels, since 2012), which detects many more small fires. Fire Calendar merges
both into **one consistent monthly record** for any place on Earth and shows it as a
year x month calendar, so you can see at a glance:

| Challenge asks for | Fire Calendar |
|---|---|
| harmonize records that cannot be compared directly | MODIS counts converted to VIIRS-equivalent counts with a ratio measured where both sensors overlap |
| a burning-activity calendar | year x month heatmap, Dec 2000 to the latest month (311+ months) |
| a selected area of interest | any country or state/province (real boundaries), any city, or any lat,lon |
| unusual conditions | every month compared with the median of the same calendar month; >= 1.5x is marked as an anomaly |
| critical periods | the burning season of each place is visible at a glance; "biggest burst" summary |
| early warning | "this month so far" card from near-real-time data, with a pace projection for the whole month |
| responders, scientists, land managers | CSV download, global time-lapse, method notes on every page |

## Method

**Data.** NASA FIRMS active fire hotspots: MODIS (Collection 6.1, standard + near-real-time)
from Nov 2000, VIIRS S-NPP 375 m (standard + near-real-time) from Jan 2012. Every hotspot
is counted in a 0.25 x 0.25 degree grid cell per calendar month.

**Harmonization.** In the overlap years 2013-2019 (84 months) both sensors observed the same
fires. For a region:

    ratio = sum(VIIRS hotspots, overlap months) / sum(MODIS hotspots, overlap months)

The ratio is measured from the region itself when it has at least 300 MODIS hotspots in the
overlap; otherwise from a wider area (4 degrees around it); otherwise from the whole world.
Examples: California 4.41, whole world 4.55.

For each month: if VIIRS data exist (from Feb 2012), the VIIRS count is used directly;
otherwise the count is `MODIS x ratio` and the month is marked **estimated** (hatched).

**Calendar statistics.**
- "usual" = median of the same calendar month over all years with data
- anomaly = month >= 1.5 x its usual value
- months whose data are not completely downloaded are shown as missing, never as zero
- months that exist only in near-real-time data are marked **provisional** (NASA reprocesses them later)
- current month: hotspots so far, projected to the full month as `count x days_in_month / days_covered`

**Regions.** Countries and states/provinces come from Natural Earth 1:10m (public domain).
A region is the set of 0.25 degree cells whose centre lies inside its boundary. Cities and
coordinates open the 1 x 1 degree cell around the point.

**Validation.** Known events stand out in the calendars: California Thomas fire (Dec 2017, ~8x usual),
Camp fire (Nov 2018, ~3.4x), August Complex season (Sep 2020, ~20x), Los Angeles fires (Jan 2025, ~2.7x).
The static site was checked against the server implementation: identical results on sample cells,
and the browser code reproduces the Python statistics exactly.

**Limitations.**
- estimated (MODIS-only) months are approximate: one constant ratio per region cannot capture
  every season or fire type (roughly +-20-30% per month)
- hotspot counts measure detected burning activity, not burned area or fire intensity
- clouds, smoke and satellite overpass times hide some fires; small fires are under-counted by MODIS
- anomalies in months with very few hotspots are mostly small-number noise

## How it is built

Everything is pre-computed, so the site is a plain static website (GitHub Pages, no server):

    backend/app/worldgrid.py      download the FIRMS archive (FIRMS API, MAP_KEY) as 0.25 deg monthly counts
    backend/app/recent.py         add the newest standard + near-real-time data
    backend/app/pack.py           pack all chunks into worldgrid/pack/*.npz (~66 MB)
    backend/build_regions.py      Natural Earth boundaries -> regions.json.gz (0.25 deg cell sets)
    backend/app/export_static.py  calendars for 4,854 regions + 19,000+ 1 deg cells -> site/data/ (~80 MB)
    site/                         index.html (globe), calendar.html, js/data.js, vendor/ (globe.gl, textures)

Update the data (Windows PowerShell, in `backend/`, with `$env:FIRMS_MAP_KEY` set):

    python -m app.recent
    python -m app.pack
    python -m app.export_static
    cd ..; git add site; git commit -m "Data update"; git push

A push to `main` that changes `site/` redeploys the site automatically (`.github/workflows/pages.yml`).

Local test: `cd site; python -m http.server 8000`, then open http://localhost:8000

## Acknowledgments

We acknowledge the use of data and/or imagery from NASA's Fire Information for Resource Management
System (FIRMS) (https://earthdata.nasa.gov/firms), part of NASA's Land, Atmosphere Near real-time
Capability for Earth observations (LANCE) (https://earthdata.nasa.gov/lance) and NASA's Earth Science
Data and Information System (ESDIS).

Boundaries: Natural Earth (public domain). Globe: globe.gl / three-globe, NASA Blue Marble texture.
NASA does not endorse this project.

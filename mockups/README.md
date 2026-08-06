# GUI direction mockups

Ten layout directions for the Operator Console, as standalone static pages. Open
any of them directly in a browser — no server, no build step:

```sh
xdg-open 01-refined-baseline.html      # or just drag into a browser
```

Each is sized to the 800×480 panel and shows the **same state**, so the
comparison is about layout and visual language rather than different content:

- 4 lanes, lane 2 active — mic live, speakers on, volume 65, level ~62%
- lane 4 also has its mic live, to show how each design surfaces a lane that's
  live but not selected
- everything else idle

These are throwaway explorations, not production code. They are self-contained
HTML with inline CSS, share nothing with `www/`, and are not deployed. Icons are
emoji placeholders standing in for the inline SVG the real UI uses.

| # | Direction | Idea |
| --- | --- | --- |
| 01 | Refined baseline | What's built now, tightened up. Lane grid left, controls right. |
| 02 | Lane-first full bleed | Lanes own the whole screen; controls in a bottom strip. |
| 03 | Side rail | Narrow vertical lane rail, controls get the rest. |
| 04 | Industrial HMI | Machine-panel language — lamps, monospace, heavy borders. |
| 05 | Daylight | Light theme for a bright environment. |
| 06 | Mixer strips | A channel strip per lane; every lane controllable at once. |
| 07 | Meter hero | Level meters are the primary object, one row per lane. |
| 08 | Minimal typographic | Almost no chrome; type carries everything. |
| 09 | Bottom tabs | Phone-app idiom — lanes as a bottom tab bar. |
| 10 | Dial | Volume as a large arc gauge with the level as an outer ring. |

## Round 2 — 11–20

Crosses of the five kept from round 1 (01 baseline, 02 lane-first, 04 industrial
HMI, 05 daylight, 06 mixer strips). The axes being separated here are **layout**
(where lanes live), **palette** (dark / industrial / daylight) and **control
model** (selected-lane only vs. every lane at once).

| # | Direction | Cross | Control model |
| --- | --- | --- | --- |
| 11 | HMI lane-first | 04 × 02 | Selected lane |
| 12 | Daylight HMI | 04 × 05 | Selected lane |
| 13 | Light mixer strips | 06 × 05 | All lanes |
| 14 | HMI mixer strips | 06 × 04 | All lanes |
| 15 | Rich lane tiles | 01 × 06 | All lanes (toggles) + selected (volume) |
| 16 | Daylight lane-first | 02 × 05 | Selected lane |
| 17 | Daylight refined + tile meters | 01 × 05 × 06 | Selected lane |
| 18 | HMI refined + tile meters | 01 × 04 × 06 | Selected lane |
| 19 | Tiles + fader column | 02 × 06 | Selected lane, fader-style volume |
| 20 | Lane rows | 06 rotated | All lanes |

15 and 20 are the two worth arguing about. **15** is a hybrid control model —
mic and speakers are per-tile and always reachable, but volume still belongs to
the selected lane, so one tap arms a lane and the big stepper stays unambiguous.
**20** is the answer to the scaling problem in 06: rows stack to 8 where channel
strips do not fit across the panel, at the cost of shorter meters.

## Round 3 — 21–30

Built from the five kept in round 2 (11 HMI lane-first, 12 daylight HMI, 16
daylight lane-first, 17 daylight refined, 19 tiles + fader). All five were
**selected-lane** designs, so that control model is settled and no longer varied
here — every mockup below uses it. What remains open is lane layout, control
placement, and palette.

| # | Lane layout | Control placement | Palette |
| --- | --- | --- | --- |
| 21 | Full-bleed tiles | Right column, fader | HMI |
| 22 | Full-bleed tiles | Right column, fader | Daylight |
| 23 | Two-panel | Right panel, fader | HMI |
| 24 | Two-panel | Right panel, fader | Daylight |
| 25 | Active lane expands | Inside the expanded lane | Dark modern |
| 26 | Full-width panel | Bottom rail | HMI |
| 27 | Full-bleed tiles + meters | Bottom rail | Daylight |
| 28 | Two-panel | Right panel, stepper | Warm paper |
| 29 | Full-bleed tiles | Bottom rail | Mono (no hue) |
| 30 | Tiles right | **Left** column | Daylight |

Three of these are arguments rather than permutations:

- **25** makes the selected lane physically larger and moves its controls
  inside it, so the thing you're touching is the thing you're changing. Its
  weakness is visible in the render — at four lanes the expanded panel has
  obvious dead space, and it would need either more per-lane content or a
  tighter layout to earn the room.
- **29** carries selection by inversion rather than hue. Nothing depends on
  colour vision, and it survives a sun-bleached or dirty panel better than any
  of the coloured schemes. Status still reads, but "mic live" is no longer
  green — worth deciding whether that matters for a safety-relevant state.
- **30** mirrors the controls to the left. Every other design here assumes
  right-or-bottom, which is only correct if the panel sits square in front of
  the operator. If it's mounted to one side, reach isn't symmetric.

## Note on 06 and 07

Both show every lane's controls simultaneously, which is a **different
interaction model** from the one currently built — they imply multi-lane control
rather than "controls follow the selected lane". Picking either means revisiting
that decision, and 06 in particular needs a real answer for what happens past 4
or 5 lanes, since channel strips don't fit 8 across on this panel.

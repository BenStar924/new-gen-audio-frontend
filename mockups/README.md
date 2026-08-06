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

## Note on 06 and 07

Both show every lane's controls simultaneously, which is a **different
interaction model** from the one currently built — they imply multi-lane control
rather than "controls follow the selected lane". Picking either means revisiting
that decision, and 06 in particular needs a real answer for what happens past 4
or 5 lanes, since channel strips don't fit 8 across on this panel.

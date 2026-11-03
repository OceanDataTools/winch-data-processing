# Test data

Real winch data the tests use, cut and compressed.

| File | Source | What it is |
|---|---|---|
| `RR2403_winch_lci90i_hydro-2024-04-06_cast.txt.xz` | R2R fileset [160178](https://doi.org/10.7284/160178), file `RR2403_winch_lci90i_hydro-2024-04-06.txt`, lines from 21:50 to 22:40 UTC | R/V Roger Revelle's hydro winch (Markey DESH-5) on RR2403, logged by OpenRVDAS from its LCI-90i at 20 Hz: the counter at a stale 664.5 m, zeroed, the cruise's one cast to 500 m, and the recovery |

R2R publishes these under the Creative Commons Public Domain Mark 1.0. Cite as: Kelley,
Katherine (2024). winch (Markey DESH-5) data as collected during the cruise RR2403, Origin and
Evolution of Intraplate Magmatism at the Revillagigedo Archipelago. Rolling Deck to Repository
(R2R). doi:10.7284/160178.

Rebuild with, from the fileset's data directory:

```bash
awk '$0 >= "2024-04-06T21:50" && $0 < "2024-04-06T22:40"' RR2403_winch_lci90i_hydro-2024-04-06.txt |
    xz -9e > RR2403_winch_lci90i_hydro-2024-04-06_cast.txt.xz
```

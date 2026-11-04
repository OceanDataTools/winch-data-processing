# RVSS Appendix A: what this tool checks

Appendix A, "UNOLS Rope and Cable Safe Working Standards", of the
[Research Vessel Safety Standards](https://www.unols.org/document/document/research-vessel-safety-standards-rvss),
11th edition (November 2021; Appendix A rewritten June 2023; last updated 25 June 2026,
[PDF](https://www.unols.org/sites/default/files/2026-10/RVSS_11_Master_Updated_17Jul2026.pdf)).
This page maps each of its requirements to what can show it was met: the winch's **data**, the
**config** (what the operator states about the tension member, sheaves and monitoring system),
or only the **operator's records**. Section numbers are the appendix's; the requirements as
data are in [`rvss.py`](../src/winch_data_processing/rvss.py).

Background: the RVTEC 2008 overview of the draft
([PDF](https://www.unols.org/sites/default/files/200810rvtap19.pdf)) set out the same structure;
the adopted text differs in places (alarms, for one: the draft required none from FS 5.0 down
to 2.5; Table A.8.2 requires them before FS 2.8), and this tool follows the adopted text.

## Definitions used

- **ABL**, Assigned Breaking Load (A.2.1): the lower of the NBL and the latest TBL; never above
  the NBL. Free-to-rotate and fixed-end conditions may have different ABLs; the config gives
  the NBL for the condition in use.
- **SWT**, Safe Working Tension (A.2.19): ABL / FS.
- **FS**, Factor of Safety (A.2.10): selected by the operator for each tension member (A.5.1.1);
  the table that holds it sets the requirements.
- **D/d, d1, w** (A.2.4 to A.2.6, A.2.23): sheave root diameter, tension member diameter,
  largest armor or outer wire, groove width.

## The tables

| | A.8.1 | A.8.2 | A.8.3 | A.8.4 | A.8.5 (synthetic) |
|---|---|---|---|---|---|
| FS | ≥ 5.0 | 2.5 to < 5.0 | 2.0 to < 2.5 | 1.5 to < 2.0, wire rope only | ≥ 5.0 |
| Tension monitoring | not required (EMT by calculation) | display 3 Hz, logged 3 Hz | display 10 Hz, logged 20 Hz, trend graph | same as A.8.3 | same as A.8.3 |
| Calibration | | every 6 months at the selected FS's load | same | same | same |
| Accuracy | | 4 % of the applied load | 3 % | 3 % | 3 % |
| Alarms (audible and visual, logged) | none | before FS 2.8 | before FS 2.2 | before FS 1.7 | before FS 5.6 |
| Sheaves and load-carrying rollers | as large as practicable | D/d ≥ 40 or 400 d1, groove ≤ 1.5 d | D/d ≥ 40 or 400 d1, grooving per handbook | same as A.8.3 | D/d ≥ 40 |
| Break tests | every 2 years (1 after a 10 % ABL decrease) | same | every year (6 months after a decrease) | same | before and after each planned use |
| Halt at | FS 5.0 or next table | FS 2.5 or next table | cables FS 2.0; wire rope 2.0 or next table | FS 1.5; slow-haul checks every ~500 m from FS 2.0 | FS 5.0, unless the Wire Pool approves |
| Operator | deemed competent | certified competent, renewed annually | same | same | same |

## Requirements and how they're checked

| Requirement | Section | Shown by | Check |
|---|---|---|---|
| Each deployment's maximum tension, the line out at it, and its maximum payout, logged | A.5.2.1, A.5.3.1, A.8.3.6 | data | the wire log's rows |
| Number and duration of deployments since the last break test | A.5.2.1 | data, across cruises | the wire record (`--wire-record`, `wire-record report`): deployments started on or after the last break test's date |
| Halt at the selected FS (tension within the SWT) | Tables A.8.1–A.8.5 | data | Tension within the safe working tension |
| Never below FS 2.0 for cables, 1.5 for wire rope, 5.0 for synthetics without approval | A.8.3, A.8.4, A.8.3.1 | data | Never below the lowest FS allowed |
| Peak tension over sheaves beyond the elastic limit (cable FS 1.8, wire rope 1.33) retires the length | A.5.6, A.6.2 | data | Peak tension within the elastic limit |
| Tension logged at 3 Hz or 20 Hz | Tables A.8.2–A.8.5 | data | Tension logging rate; Gaps in the tension record |
| Tension displayed at 3 Hz or 10 Hz, with a trending graph | Tables A.8.2–A.8.5 | config | Tension display refresh rate |
| Calibration every 6 months | Tables A.8.2–A.8.5 | config | Tension measuring system calibration |
| Accuracy 4 % or 3 % of the applied load | Tables A.8.2–A.8.5 | config | Tension measuring accuracy |
| Alarms before the table's FS | Tables A.8.2–A.8.5 | config | Tension alarm setting |
| Alarm conditions included in the logged data | Tables A.8.2–A.8.5 | data, where the format logs them | Alarm conditions in the logged data (info until a format carries them) |
| D/d, D/d1 and groove of every sheave and load-carrying roller | A.8.2, Tables A.8.2–A.8.5 | config | Sheaves and load-carrying rollers |
| Break test interval, shortened after a 10 % ABL decrease | A.5.1.1, Tables A.8.1–A.8.4 | config | Break test interval |
| Retire below 50 % of NBL | A.5.6 | config | Assigned breaking load |
| The FS documented for each tension member | A.5.1.1 | config | the wire log's header |
| Wire log: NSF identifier, winch and system manufacturer, sheave train (D and w for each sheave) | A.5.2.1, A.5.3.1 | config | the wire log's header |
| Wire log: spooling operations and cutbacks, lubrication, TDR / resistance / OTDR results, terminations and proof tests | A.5.2.1 | operator's records | not in the winch's data |
| Wire log transfers with the tension member; the NSF Wire Pool database recommended | Tables A.8.1–A.8.5 | operator | the wire log is a file to upload |
| Slow-haul checks every ~500 m once FS 2.0 is reached: "halting a deployment at regular intervals (~ 500 m) and conducting a slow haul until the nominal and peak tensions are established and verified" | Table A.8.4 | data (the halts); operator (the tensions verified) | Slow-haul checks below FS 2.0: a halt (the payout not growing for 30 s, a slow haul included) at least every 550 m paid out from where FS 2.0 was reached to the deepest payout |
| Estimated Maximum Tension within the SWT before a deployment | A.8.1 | operator | not data: a plan, A.9's examples |
| Termination plan, proof tests | A.5.1.2 (by 2028-01-01) | operator's records | — |
| Operator deemed or certified competent | Tables A.8.1–A.8.5 | operator's records | — |
| Danger zones, barriers, warning notices | Tables A.8.1–A.8.5 | operator | — |
| Lubrication every 12 months; fresh-water wash down each cruise | A.5.8, A.5.9 | operator's records | — |
| Synthetics: visual inspection, creep (10 % between picks) | A.8.3.4, A.8.3.8 | operator's records | — |

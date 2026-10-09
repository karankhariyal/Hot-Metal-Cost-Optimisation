# MBF burden optimiser (V7)

Least-cost burden for one tonne of hot metal (dry and net kg) on the Hospet Steels mini blast furnaces, as a Streamlit dashboard.
The engine is the notebook **v11.7** model: ideal plant conditions, the thumb rules agreed in the Sep-26 plant review, the
hot-zone heat balance (v11.6) and the heat audit on real plant months (v11.7).

## Files

| File | What it is |
|---|---|
| `app.py` | The dashboard (front end). |
| `optimiser.py` | The engine (backend). No Streamlit code in it, so it can also be imported in a notebook. |
| `analytics.py` | Dashboard analyses (slag oxides, trends, rules of thumb, scenarios, run history). They only re-solve the engine; they never change it. |
| `requirements.txt` | Deployment dependencies. |
| `MBF_Input_Template.xlsx` | Input template. One sheet, one row per material. **Demo values only.** |
| `.streamlit/config.toml` | Dark theme and upload limit. |
| `tests/` | `test_engine.py` (the notebook's full self-test plus dashboard checks), `test_analytics.py` (the V6 analyses) and `test_app.py` (drives every page and the Adjust inputs panel). |

## Deploy

1. Put these files in a **private** GitHub repository, keeping the folders (`.streamlit/`, `tests/`). Do not commit real prices or stock; upload the master workbook in the app instead.
2. On Streamlit Community Cloud choose the repository, branch `main`, main file `app.py`.
3. Prices and costs are commercially sensitive. Check that the platform's viewer restrictions meet your company's rules, or host it on an internal server.

Run locally: `pip install -r requirements.txt` then `streamlit run app.py`.

## Using it

1. **Upload & settings**: download the template, fill it with plant values, upload it and press *Activate this master*. Until you do, the dashboard shows a demo-data notice.
2. **Inputs**: check the policy, slag limits and fuel-rate rules. Defaults are the plant values from the thumb-rule review. Each fuel rule has an on/off switch.
3. **Materials & stock**: switch materials on or off and adjust price, moisture, chemistry and stock. Press *Apply changes*.
4. **Dashboard**: press *Run optimiser*. Any later change shows "Rerun needed" until you run again.
5. **Scenario analysis**: sinter sweep, assay and price sensitivity, tornado and sinter break-even.
6. **Reports & export**: press *Export optimised results*, then *Download workbook*. Nothing downloads on its own.

| Page | Purpose |
|---|---|
| Dashboard | Key numbers, the recipe straight under them, "vs last run" line, Adjust inputs panel (every model input), save a run as a scenario, what set the sinter share, coke and cost at every sinter share from 0 to 100 %, where the CaO / SiO2 / Al2O3 come from, burden and cost charts, limit bars, fuel gauges, heat-balance cards, run history, notes |
| Inputs | Policy (sinter guard rails, pin, Fe/C guide), slag limits, Ks and the MgO/Al2O3 guide, fuel-rate rules with the eight rule switches and the reference charge rates, stock, price basis and model constants, heat balance (mode, every parameter, calibrate) |
| Materials & stock | The editable material table |
| Burden & cost | Recipe, slag, oxide sources, fuel rate, Fe impact, moisture, stock use, the 0-100 % sinter curve table, heat balance (every term, vs the thumb rules), diagnostics |
| Slag oxides | CaO, SiO2, Al2O3 and MgO against their limits, by material or by group; oxide per tonne of Fe delivered; oxides across 0-100 % sinter; one-assay what-if |
| Trends | The model's rules of thumb on this run; cost and coke vs sinter share (with saved scenarios overlaid), vs ore / sinter Fe, heat map of sinter share x Fe, ore price scan with switch points, sinter basicity what-if, run history and scenario comparison |
| Scenario analysis | Sinter sweep (with oxides by sinter share), assay sensitivity (Fe, SiO2, Al2O3, CaO, MgO, moisture), price sensitivity, tornado, sinter break-even |
| Reports & export | The formatted Excel workbook, including Sinter Curve 0-100% and Heat Balance sheets and one sheet per scenario tool you ran |
| Heat audit | Template, plant-month upload, the audit (carbon and nitrogen balances, DRR two ways, hot-zone cooling losses, heat residual, DRR vs sinter fit), apply to the model, results workbook |
| Upload & settings | Master workbook, template, resets, self-test |

## What changed after V7 (October 2026)

- **O&M cost.** Inputs > Stock and pricing (and the Adjust inputs panel) has *O&M cost, Rs per tonne of hot metal* (`Config.om_rs_thm`, default 0).
  It is added to the raw-material cost in every reported cost per tHM, appears as its own row in the burden table and as a slice of the cost
  donut, and is listed in the Run Settings sheet. It is a constant, so it never moves the burden: at 0 every V7 number is unchanged
  (the demo answer is still Rs 20,033.29 per tHM at 76.7 % sinter).

## What V7 adds

- **Engine v11.7** (`optimiser.py`, engine version 14.0). The notebook's hot-zone heat balance and heat audit, with the notebook's
  full self-test (119 checks). Check mode is the default and leaves the optimum unchanged: the demo answer is still
  Rs 20,033.29 per tHM at 76.7 % sinter. Floor mode makes the LP also meet the heat-balance carbon need.
- **Every input in the Adjust inputs panel** (Dashboard). Eight tabs: Materials (every column of every material, on or off; add or
  remove a material), Prices & on/off (all materials), Sinter & fuel policy, Slag, Hot metal & Fe, Fuel-rate rules (switches,
  coefficients, references, base fuel per furnace, reference charge rates), Heat balance (mode, all parameters, calibrate to the last
  run, reset to placeholders), Stock & constants. A test checks that every setting of the model has a control in the panel.
- **Heat settings are per session.** They live in `Config.heat` with everything else, so one user's calibration never reaches another.
- **The remaining model constants** are now settings too: reference charge rates behind the per-point rules, raw-flux threshold,
  direct-reduction degree for the carbon floor, Mn reduction efficiency, MgO/Al2O3 and Fe/C guides.
- **Heat audit page** and **Heat balance** views on the Dashboard, the 0-100 % curve (heat-balance minimum coke, dashed), Burden & cost
  and the Excel export.
- Left out on purpose: the "v12 calibration layer" that was appended to one copy of the notebook. It can overwrite the plant sinter
  rule and the 96.5 Fe closure without a reversible switch, and its guardrails were never enforced.

## What V6 added

- **Recipe first.** On the Dashboard the recipe table sits directly under the key numbers; everything else follows it.
- **Adjust inputs** (Dashboard): a panel with Sinter, Slag, Fuel, Prices and Assays tabs. Edits are a draft until *Apply and run*;
  *Cancel* discards them and *Plant default settings* resets the draft's settings. It edits the same settings as the Inputs and
  Materials & stock pages. After a run the Dashboard shows the change against the previous run and what changed.
- **Slag oxides** and **Trends** pages (see the table above). They work on the inputs and settings of the last run, so they always match
  the Dashboard, and they say so when an input has changed since.
- **Saved scenarios**: up to 8 per session, compared three at a time and overlaid on the sinter-share curves.
- **Excel**: the workbook also carries the Trends and Slag oxides tables made from the exported run, the run history and the saved scenarios.
- **Engine**: unchanged model. The only change in `optimiser.py` is an optional `extra=` argument on `write_export` / `export_bytes`
  that lets the dashboard add its sheets (engine version 13.1).

Every V6 figure is calculated live by re-solving the model on your data. The sinter basicity what-if assumes the sinter price does not
change and does not model sinter strength or reducibility; the page says so. Plant actuals upload (calibrating base fuel against daily
coke rates) is not in V6; the V7 heat audit covers the heat model from monthly plant data.

## The model (v11.7)

- **Sinter : ore is a free choice** inside 50-80 % guard rails, solved as an exact LP at every sinter share. Every run says what set the share: the cost balance, a chemistry limit, or a guard rail. The share can be pinned anywhere from 0 % to 100 % for what-if runs.
- **Fuel rule** = base 545 kg/tHM (both furnaces) + sinter share (1 kg per point vs 65 %) + ore Fe + sinter Fe (3 kg per point) + slag (0.18 kg/kg over 330) + raw flux (0.30 kg/kg) + ore moisture + coke moisture + sinter/flux/minor moisture (an extension, not a plant rule). PCI 120 and nut coke 40 kg/tHM are fixed; regular coke is the residual.
- **Slag**: B2 0.99-1.01, MgO 7-8 %, Al2O3 17-18.5 %. MgO/Al2O3 is reported against a 0.40-0.55 guide. Predicted hot-metal S uses a fixed Ks of 25 on all charged sulphur.
- **Heat balance** (hot zone below the thermal reserve zone): tuyere carbon heat plus the hot blast must cover direct reduction, Si / Mn reduction, carbon dissolving in the metal, late raw-flux calcination and its solution loss, heating the metal and slag, cold PCI and the lower-furnace losses. Every term is linear in the burden. *Check* (default) reports carbon needed vs charged and the heat-balance minimum fuel; *floor* also enforces it (fuel = the larger of the plant rule and the heat need). All heat values are literature placeholders until calibrated with a run or the heat audit.
- **Heat audit**: one workbook per plant month; carbon balance, nitrogen balance for the top-gas volume, DRR from the gas and from the carbon charged, hot-zone cooling losses, measured heat residual (never forced to zero), thumb-rule fuel vs fuel charged, and DRR vs sinter share when at least 3 usable months span 5 points (applied only if |t| >= 2). Nothing is applied until you press *Apply to model*.
- **Removed in the plant review**: PCI rate / FC / moisture rules, coke ash, coke CSR, DRI credit, hot blast, furnace offset, the Mar-26 burden-Fe form, and the ore-grade cost model (page, export and "set sinter price"). Old master files with Ash / CSR columns still load; those columns are ignored with a note.

## Same as the notebook, and what the dashboard adds

**Same:** the LP, the sinter share search, stock balancing (relaxed as little as possible), limit diagnostics, the fuel-rate rules, the notes, the loader, the Excel workbook and the scenario tools. `run_self_tests(full=True)` in `optimiser.py` is the notebook's own self-test.

**Added:**
- Settings live in a `Config` object, one per browser session. `session(cfg)` loads it into the engine for one call and restores the defaults afterwards, behind a lock, so two users' settings can never mix (tested).
- The Excel workbook is built in memory. Nothing is written to the server.
- Input checks flag suspicious prices (for example a flux priced above iron ore).
- Interactive Plotly charts in place of the notebook's static ones.

## Tests

`pip install -r requirements-dev.txt` then `pytest -q` (78 tests, about four to seven minutes; the tornado and break-even checks are the slow part).
The demo answer is pinned as a regression anchor (Rs 20,033.29 per tHM at 76.7 % sinter).

## Things to know

- Prices and assays in the built-in table are **placeholders**. Hot-metal C % (4.3) is a placeholder too.
- A normal run takes about 3 s: the burden itself takes under a second, and the 0-100 % sinter curve (41 extra solves) the rest. A tornado takes 20-40 s. Solves are queued through one lock.
- The Trends page takes a few seconds the first time it opens after a run (it re-solves the model for every tab); after that it is instant until an input changes. Results are cached per session.
- HiGHS is used as the solver when `highspy` is installed (it is in `requirements.txt`); otherwise PuLP's CBC. The answers are the same.
- `requirements.txt` keeps PuLP below 4 and pandas below 3. PuLP 4 changes the solver call; move to it deliberately.

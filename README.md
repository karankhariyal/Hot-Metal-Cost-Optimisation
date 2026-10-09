# Hospet Cost Studio

One app for the cost of one tonne of hot metal (Rs/tHM): the **sinter model**, the **MBF (furnace) model** and a
**combined model** that feeds the sinter plant's result into the furnace. The two original dashboards are kept whole.

    pip install -r requirements.txt
    streamlit run app.py

Python 3.10+ recommended. Google Fonts and the Material icon font need internet; every font has a fallback.

## What is in it

| Workspace | Pages |
|---|---|
| **Combined cost model** (landing) | Hot metal cost, Uploads & shared settings, Basicity scan, Price drivers, Saved scenarios, Export, Glossary |
| **Sinter-to-hot-metal impact** (new) | Baseline & changes, Impact, Optimise sinter inputs, What each condition costs, Impact scenarios, Export |
| **Sinter model** (original dashboard, 12 pages) | Upload & Settings, Inputs, RM Stock & Materials, Dashboard, Recipe & Composition, Inventory Usage, Manual Burden Control, Scenario Analysis, Plant Run Validation, Productivity, Wet Specific Consumption, Reports |
| **MBF model** (original dashboard, 10 pages) | Upload & settings, Inputs, Materials & stock, Dashboard, Burden & cost, Slag oxides, Trends, Scenario analysis, Reports & export, Heat audit |

## Navigation

One sidebar, no model switch: a search box, then four collapsible sections (Combined, Impact, Sinter, MBF) whose pages are rounded pills with
an icon. The arrow at the top right folds the sidebar into an icon rail (the four models, then the pages of the current one).
Colours and fonts are the shared theme in `shared/theme.py`.

## How the combined model works

1. The sinter model runs on its own Excel file. Its result (price = dry raw-material cost + sinter O&M, plus Fe, CaO, SiO2,
   Al2O3, MgO) replaces the furnace table's one switched-on **Sinter** row. Moisture, S and Mn stay from the MBF Excel.
2. The furnace model runs and says how much sinter it uses (kg/tHM). Times the plan (tHM) that is the sinter tonnage
   needed, and the sinter model runs again at that tonnage. This repeats until the tonnage settles.
3. Rules: the run reports whether it converged; a failed sinter run stops the loop with the sinter model's message;
   sinter is capped at what the plant can make within tolerance (Optimal or Relaxed), and the furnace then takes more
   ore for the rest; sinter and furnace materials, stock and stock checks are never mixed, even when names match.
4. **Furnace O&M** (Rs per tonne of hot metal, default 0) is added to the raw-material cost, so the hot metal cost is
   raw materials (sinter row included, with its own O&M) + furnace O&M. Set it under *Uploads & shared settings* or *MBF > Inputs*; it is one setting.

**Optimise hot metal cost** is the single model and the landing page of the Combined cost model. One objective: the lowest cost of one tonne of hot metal.
One press runs the combined loop on the current inputs when that has not been done (no separate "Run both models" step) and then searches for the sinter
recipe the furnace likes best (`combined/cooptimise.py`, `optimise`; the page is `combined/single.py`). Every sinter spec tier, tolerance, stock rule, ore-ratio rule,
FeO band, heat balance and furnace rule stays as set. Two tick boxes change a condition and are **off by default**: holding the ores less strictly to their stock
shares, and letting the sinter use its approved tolerance (the spec windows are opened to the plant-approved tolerance edges; the result names every goal that leaves
its spec, and the saving is always measured against today's recipe under today's conditions). On the sample files neither finds a saving: the sinter model picks the
cheapest recipe for the sinter plant, and with the windows opened it chooses one that costs the furnace more. The page shows the Now | Optimised cards, the two KPI
panels, the sinter and furnace burdens and results with their limit cards, what moved the cost, and, folded, what the furnace pays and the search trail.
The older *Optimise sinter inputs* page in the impact model uses the same result.

The **Hot metal cost** page reads top to bottom: the three stage cards (sinter price, furnace coke, hot metal cost with the plan cost), two **KPI panels**,
**Where the money goes** with the alerts, **Quality limits**, then the burden compositions. The KPI panels are one per plant. *Sinter plant* (per tonne of
sinter): mill scale used (kg/t, % of burden, tonnes in all), Fe, Al2O3, basicity, SiO2, MgO, coke used (with which cokes), then the iron ore used (kg, % of
the fresh burden, and which ores; mill scale counts as iron ore there because the sinter model groups it that way). *Blast furnace* (per tonne of hot metal):
sinter as % of the sinter + ore mix, PCI, slag volume, flux used (kg and which), then the iron ore used (kg, % of the sinter + ore mix, and which ores).
**Quality limits** is one small card per limit: green inside the target, amber outside it but inside the approved tolerance (the sinter SiO2 case), red outside
the tolerance or a furnace limit; a line above says how many are inside. The page has no stock-check section any more (the stock numbers are on the Sinter
and MBF pages, the Export workbook still carries both stock checks, and the alerts still say when an ore uses all of its stock). The baseline, last-month and
budget boxes are folded under the KPI panels. Management view and Engineer view both show the KPI panels and the quality limits; Engineer view adds the
cost breakdown, the change since the last run, more values and the workings. The numbers are built in `combined/kpis.py` (no Streamlit code).

The **Hot metal cost** page shows both burdens: the sinter burden (dry kg and Rs per tonne of sinter, BF returns at zero cost) and the
furnace burden (dry kg and Rs per tHM, with O&M as its own row). Each table's total ties to the model's own cost.

### One answer on every page

The MBF workspace has a switch, **Sinter row in the furnace table**: *From sinter model* (default) or *Typed row* (the original MBF behaviour).
In the default mode the Sinter row takes the **combined run's** sinter result whenever a combined run is current (same sinter tonnage, same
sinter cap, same plan), so the MBF page and the combined page give the same sinter share and cost. Before any combined run it takes the sinter
dashboard's own last run (its planning tonnage) and says so; that run can differ from the combined result because the combined loop re-runs the
sinter model at the tonnage the furnace needs. Changing any input marks the combined run stale and the MBF page falls back to the sinter dashboard's run until you run both again.
Plan (tHM) sets the furnace's stock caps and the sinter tonnage; the MBF page follows the combined plan while it uses a combined run.

## Sinter-to-hot-metal impact (the fourth model)

Everything where you change something in the sinter making and ask "what does this do to hot metal?" lives here. The sinter, MBF and
combined models are not changed: the combined run is the baseline, read-only, and every change is applied to **copies** of its inputs,
so a result here is exactly what the combined model gives with those inputs.

- **Baseline & changes:** one panel for every sinter-making lever: material prices and stock (percent), sinter O&M, IOL Fines and BFR
  percentages, the spec windows, the tolerance edges, the coke band and the stock-ratio strictness. Base and changed values are shown.
- **Impact:** hot metal cost now against with the changes, and two bridges. *By lever:* the changed lever groups are added one after
  another in a fixed order (prices, stock, O&M, returns, spec windows, tolerances, coke band, stock rule) and the loop re-run each time.
  *By path:* the furnace's Sinter row is moved from its old to its new value one field at a time (price, Fe, Al2O3, MgO, SiO2, CaO,
  availability) and the furnace re-solved. Both bridges add up exactly to the total; the slices depend on the order, the total never does.
- **Optimise sinter inputs:** moved here from the combined model (below).
- **What each condition costs:** relaxes ONE rule at a time (stock-ratio rule, basicity, MgO, CaO, SiO2 and Al2O3 windows, SiO2, MgO,
  Al2O3 and Fe tolerance edges, coke band) and ranks them in Rs per tHM, with the sinter chemistry each relaxation produces. It prices
  the rules; it does not say which are safe to relax. On the sample files the SiO2 tolerance edge is the rule worth money (about Rs 81 per
  tHM for 0.2 points); most others cost nothing measurable because stock and price set the recipe.
- **Impact scenarios** and **Export** (one workbook: changes, both bridges, furnace response, sinter chemistry and inputs, furnace burden,
  condition prices, saved results).

Small effects (about Rs 50 per tHM or less) can come from the loop's own tolerance. Numbers rest on the furnace thumb rules and on the sinter
tolerances, which are placeholders except SiO2 6.2; the model has no sinter strength or reducibility terms.

## Optimise sinter inputs

The sinter model meets its spec tiers first and then picks the cheapest recipe among those that qualify. That recipe is not necessarily the
best for the furnace: a little more Fe or MgO, or a little less Al2O3, can cut coke, slag and flux by more than it adds to the sinter's
price. This page finds the sinter recipe that gives the lowest cost of one tonne of hot metal, **inside every existing condition** (spec
tiers, tolerances, stock, ore-ratio rule, heat balance, FeO band, furnace rules).

How: it measures what the furnace pays for each point of sinter Fe, MgO, Al2O3, SiO2 and CaO (central differences on the sinter row),
hands Fe, MgO and Al2O3 to the sinter model as a credit in its **last, cost step only** (`chem_credit`, off by default), re-runs the
combined loop at several credit strengths, and repeats from the best recipe. The best candidate is re-run at full precision and accepted
only if it saves at least the chosen amount (default Rs 50 per tHM, the loop's own noise) and misses no sinter goal by more than today's
recipe. Today's recipe is always a candidate, so the answer is never worse. When nothing qualifies the page says so.

**What the page shows.** Always both plants, whether or not a saving was found. Before any search it shows *Today's recipe*; after a search it shows today
against optimised (a Change column marks what moved; with no accepted saving the optimised recipe is today's and the saving card says so). *Sinter plant*: the
burden (dry kg and Rs per tonne of sinter, BF returns at zero cost, total = the sinter's raw cost), the results (price, Fe, Al2O3, basicity, SiO2, MgO, CaO,
coke, mill scale, iron ore %, tonnes made, status) and the five sinter quality-limit cards. *Blast furnace*: the burden (dry kg and Rs per tHM, total = the hot
metal cost), the results (hot metal cost split, sinter % of the mix, coke, nut coke, PCI, fuel rate, slag volume, flux, iron ore, slag B2 / MgO / Al2O3, hot metal
S, status) and the four furnace limit cards. With a saving, *What moved the hot metal cost* shows the change by cost group (sinter bought, iron ore, coke, nut
coke, PCI, flux, O&M); the bars add up exactly to the total change. The tables are built in `combined/optview.py` (no Streamlit code); sinter and furnace
materials stay in separate tables even when names match.

Read this before expecting a saving. On the sample files, with every condition kept, the search finds about Rs 2 to 4 per tHM and keeps
today's recipe: the pinned sinter goals and the ore mix held to the stock ratio leave almost no room. The one condition that does leave
room is how strictly the sinter model keeps the ores to their stock shares (inventory weight). A tick box, **off by default**, also tries
weights 0.5 and 0; a result that needs it says so, gives the ore drift it causes, and leaves the decision to you (on the sample files with
four times the stock it is worth about Rs 108 per tHM). The model has no sinter strength or reducibility terms, so check a recommended
recipe against plant experience.

## Editing what you upload

*Uploads & shared settings* (both files) and *Sinter model > Upload & Settings* (the editor lives in `sinter/app.py`, drawn by `shared/editor.py`) show an editable table under the uploader: on/off, stock, price,
chemistry, Tech min/max (sinter), moisture, group, and add or delete rows. **Nothing changes until you press Confirm changes**; *Discard edits*
throws them away. Bad values (negative, above 100 %, duplicate names, ...) are refused with the reason. The furnace table is the MBF dashboard's own,
so *MBF > Materials & stock* sees the same edit.

## Known behaviour to read before trusting a number

* **Knife-edge near the sinter ceiling.** With the sample files the furnace's sinter demand can flip between two burdens
  within a tonne or so of sinter, because several slag limits bind together. The loop narrows it to about half a tonne
  and shows the result from the side where the plant makes at least what the furnace uses. Differences of about
  Rs 50/tHM or less are noise. The page says so when it happens.
* **Relaxed sinter.** The sample sinter misses SiO2 (6.07 against 5.8) and Al2O3/SiO2 but is inside the approved
  tolerance, so the hot metal cost is an estimate.
* A combined run takes a few to 25 s; price drivers about a minute; scans about 20 s per few points.

## Layout

    app.py                 router, sidebar (search, sections, icon rail), MBF sinter-row banner
    combined/loop.py       the loop (no Streamlit code): convergence, cap, failure stop, knife-edge, burden compositions
    combined/handoff.py    sinter -> furnace row (combined run first), stale detection, input diffs, the two stock checks
    combined/page.py       the combined pages
    combined/single.py     the Optimise hot metal cost page (the single model)
    combined/optview.py    the Optimise page's burden, results and cost-bridge tables (no Streamlit code)
    combined/kpis.py       the KPI panels and quality-limit cards of the Hot metal cost page (no Streamlit code)
    combined/cooptimise.py the sinter-recipe search for the lowest hot metal cost (no Streamlit code)
    impact/engine.py       the fourth model's engine: change set, bridges, condition prices (no Streamlit code)
    impact/page.py         the fourth model's pages
    shared/editor.py       editable material tables with a Confirm step (sinter file, furnace file)
    shared/nsrun.py        runs the original dashboards (see below)
    shared/theme.py        palette, fonts, one stylesheet for all three workspaces, page grid
    sinter/                sinter dashboard (app.py) + engine (optimizer.py); engine: one optional input added (`chem_credit`, the last cost step only; with it off the results are identical to before), app.py gained the edit-and-confirm table on Upload & Settings
    mbf/                   MBF dashboard + engine (optimiser.py, analytics.py) + its tests -- V7 plus the O&M setting
    sample_inputs/         SInter_Input.xlsx, MBF_Input.xlsx
    tests/                 combined tests; ENGINE_HASHES.txt records the SHA-256 of the engine and dashboard files

### How the originals are reused

`shared/nsrun.py` reads each original `app.py`, applies a few mechanical rewrites **in memory**, and runs it: it drops
`set_page_config`, the dashboard's own CSS and sidebar, gives each dashboard a prefixed view of `st.session_state`
and prefixed widget keys (`sinter__`, `mbf__`) so they never collide, and maps the old colours and fonts to the shared theme.
Every page function, table, chart and export is the original code. Both still run alone: `streamlit run sinter/app.py`, `streamlit run mbf/app.py`.

## Tests

    pip install -r requirements-dev.txt
    pytest -q tests                  # combined tests
    PYTHONPATH=. pytest -q mbf/tests # the MBF tests (O&M defaults to 0, so every V7 number is unchanged)

`tests/test_engines_unchanged.py` fails if an engine or dashboard file differs from `ENGINE_HASHES.txt`. The sinter engine changed only by the optional `chem_credit` input (its line was regenerated; `tests/test_cooptimise.py` proves identical results with it off); the sinter dashboard's line was regenerated for the uploaded `app.py` (which now includes the upload-page editor) and the
MBF lines when the O&M setting was added.

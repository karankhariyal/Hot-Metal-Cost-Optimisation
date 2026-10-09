# Page-by-page checklist (tick as you go)

Start: `streamlit run app.py`. Use the sample files in `sample_inputs/`.

## Combined cost model
- [ ] Optimise hot metal cost (landing page of the Combined model): one button runs both models and then the search; Plan, smallest saving, rounds and the two option tick boxes (off by default)
- [ ] After a press: Now | Optimised | Saving cards, Key numbers (sinter and furnace panels), burdens and results for both plants with limit cards, what moved the cost (with a saving), folded trail
- [ ] With "use the approved tolerance" ticked and a saving: the amber notice names the goals that use the tolerance; the limit cards judge the recipe against today's spec
- [ ] Uploads & shared settings: both file cards show; plan, tolerance, passes editable; sinter and furnace upload tabs work
- [ ] Hot metal cost: before a run it says "No run yet"; Run both models gives the three-stage hero, chips, money table, alerts
- [ ] Under the three stage cards: a Sinter plant panel (mill scale used, Fe, Al2O3, basicity, SiO2, MgO, coke used, iron ore used with which ores) and a Blast furnace panel (sinter % of the mix, PCI, slag volume, flux used with which fluxes, iron ore used with which ores)
- [ ] Quality limits: one card per limit, green / amber / red edge, one line saying how many are inside their target; no Stock check section on this page
- [ ] "Compare with a baseline run, last month or budget" is folded under the panels and still works
- [ ] Engineer view adds the cost breakdown, change since last run and "More values"; Management view keeps the panels and quality limits but not those
- [ ] Change a sinter price, the "Inputs changed since this run" chip appears; run again and Rs/tHM moves
- [ ] Use this run as baseline, then "Change vs baseline" reads No change; last-month and budget boxes show differences
- [ ] Change since last run names what you changed
- [ ] Basicity scan, Price drivers, Saved scenarios (save two, compare), Export (workbook downloads), Glossary

## Sinter-to-hot-metal impact
- [ ] Before a combined run: every page says to run Hot metal cost first and offers a button to open it
- [ ] Baseline & changes: change a price, the stock of one ore, sinter O&M, a spec; the Changes table lists base and changed; Reset clears it
- [ ] Run impact lands on Impact: three cards, a lever bridge, a path bridge (both add up to the change), furnace response, sinter chemistry and inputs
- [ ] The Hot metal cost page and the combined run are unchanged afterwards
- [ ] Optimise sinter inputs: says honestly when nothing is left to gain; the stock-ratio tick box is off by default
- [ ] Optimise sinter inputs, before any search: "Today's recipe" with the sinter burden, sinter results and limit cards, then the furnace burden, results and limit cards
- [ ] After a search with no accepted saving: the same tables stay, the Saving card says "No saving accepted"; with a saving: Now | Optimised columns and "What moved the hot metal cost" (bars add up to the total change)
- [ ] Burden totals: sinter = sinter raw cost, furnace = hot metal cost; the same ore name (KIOM) appears in the sinter table and the furnace table as separate materials
- [ ] What each condition costs: one row per rule, most saving first; Export and Impact scenarios work

## Sinter model (all 12 pages unchanged)
- [ ] Run optimizer on the Dashboard; every page opens; nothing from MBF leaks in

## MBF model (all 10 pages unchanged)
- [ ] Before the sinter model has run: banner says so, Run optimiser is blocked
- [ ] After: banner shows the sinter row values; Materials & stock shows the replaced row; Run optimiser works
- [ ] Typed row mode restores the typed sinter values

## Cross-checks
- [ ] Sinter alone: Rs 6,907.94 raw, Rs 7,657.94 with O&M on the sample file (12 materials on, 10,000 t, 7 days, IOL 8%, BFR 17%, O&M 750)
- [ ] MBF alone with that sinter row, Ore-3, Coke-1, BHQ off: Rs 31,039 at 77.3% sinter (plan tHM off)

## Added in October 2026
- [ ] Sidebar: no Combined/Sinter/MBF radio; each section opens, active page is a pill; search "heat" lists Heat audit; the arrow folds to an icon rail and back
- [ ] Uploads & shared settings: "Furnace O&M, Rs per tHM" next to Plan; changing it shows "Inputs changed since this run"; run again and Rs/tHM rises by exactly that amount
- [ ] MBF > Inputs shows the same O&M value; MBF Dashboard cost card reads "Cost incl. O&M" with the raw + O&M split
- [ ] Hot metal cost: "Burden composition" shows the sinter burden and the blast furnace burden; totals equal the sinter raw cost and the hot metal cost
- [ ] Uploads & shared settings (both tabs) and Sinter > Upload & Settings: edit a price, an availability tick and a chemistry value, press Confirm changes; the other pages show the edit; Discard edits restores the file
- [ ] After "Run both models": MBF > Dashboard > Run optimiser gives the same sinter share and cost as the combined page (banner says "Sinter row from the combined run")

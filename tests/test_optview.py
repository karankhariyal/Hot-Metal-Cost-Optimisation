"""The Optimise page's burden / results / bridge tables (combined/optview.py) and the page itself, with and without a saving."""
import pandas as pd
import pytest

from helpers import KW, real_inputs, sinter_active
from combined import cooptimise as C
from combined import loop as L
from combined import optview as O
from sinter import optimizer as s
from mbf import optimiser as m

PLAN = 9000.0


def _run(om):
    sdf, mdf = real_inputs()
    sdf = sinter_active(sdf)
    r = L.run_loop(sdf, KW, s.TARGETS, om, mdf, m.Config(), PLAN)
    r["inputs"] = {"sdf": sdf, "mdf": mdf, "om": om, "kw": KW, "targets": s.TARGETS, "mcfg": m.Config(), "plan": PLAN}
    return r


@pytest.fixture(scope="module")
def pair():
    return _run(750.0), _run(700.0)                      # a cheaper sinter O&M stands in for an optimised recipe


def test_burden_totals_tie_to_the_models_own_cost(pair):
    a, b = pair
    sb, fb = O.sinter_burden(a, b), O.furnace_burden(a, b)
    assert sb.iloc[-1]["Material"] == "Total" and fb.iloc[-1]["Material"] == "Total"
    assert fb.iloc[-1]["Now Rs/tHM"] == pytest.approx(L.summary(a)["hm_cost"], abs=0.01)
    assert fb.iloc[-1]["Optimised Rs/tHM"] == pytest.approx(L.summary(b)["hm_cost"], abs=0.01)
    assert sb.iloc[-1]["Now Rs/t"] == pytest.approx(L.summary(a)["sinter_raw"], abs=0.01)
    assert fb.iloc[-1]["Change Rs/tHM"] == pytest.approx(L.summary(b)["hm_cost"] - L.summary(a)["hm_cost"], abs=0.01)


def test_today_only_has_no_optimised_columns(pair):
    a, _ = pair
    for d in (O.sinter_burden(a), O.furnace_burden(a), O.sinter_results(a), O.furnace_results(a)):
        assert not any("Optimised" in c or "Change" in c for c in d.columns)


def test_sinter_and_furnace_materials_stay_separate(pair):
    a, b = pair
    sm_ = set(O.sinter_burden(a, b)["Material"])
    fm_ = set(O.furnace_burden(a, b)["Material"])
    assert "KIOM" in sm_ and "Ore-1_KIOM" in fm_ and "KIOM" not in fm_      # same ore, two tables, never merged


def test_bridge_adds_up_exactly(pair):
    a, b = pair
    rows = O.cost_bridge(a, b)
    assert sum(d for _l, d in rows) == pytest.approx(L.summary(b)["hm_cost"] - L.summary(a)["hm_cost"], abs=1e-6)
    assert dict(rows)["Sinter bought"] == pytest.approx(L.summary(b)["groups"]["Sinter"]["rs"] - L.summary(a)["groups"]["Sinter"]["rs"])


def test_results_rows_match_the_summary(pair):
    a, _ = pair
    sm = L.summary(a)
    fr = O.furnace_results(a).set_index("Item")
    assert fr.loc["Hot metal cost", "Now"] == pytest.approx(sm["hm_cost"]) and fr.loc["Slag volume", "Now"] == pytest.approx(sm["slag_kg"])
    sr = O.sinter_results(a).set_index("Item")
    assert sr.loc["Sinter price (raw + O&M)", "Now"] == pytest.approx(sm["sinter_price"]) and sr.loc["Fe", "Now"] == pytest.approx(sm["sinter_ach"]["Fe"])


# ---- the page
def _page(at):
    at.session_state["ws"], at.session_state["route_impact"] = "impact", "Optimise sinter inputs"
    at.run()
    return " ".join(x.value for x in at.markdown)


def test_page_shows_both_plants_before_and_after_a_search_that_keeps_today_s_recipe():
    from test_app_flow import fresh_app, go, press
    at = fresh_app()
    go(at, "combined", "Hot metal cost")
    press(at, "Run both models")
    t = _page(at)
    assert not at.exception, at.exception[0].value if at.exception else ""
    assert "Today's recipe" in t and "Sinter plant" in t and "Blast furnace" in t and "Quality limits, today" in t     # before any search
    run = at.session_state["cmb_run"]
    res = {"ok": True, "accepted": False, "message": "No saving.", "conditions_changed": False, "gain": 12.0, "fp": run["fp"], "run_time": run["time"],
           "values": C.furnace_values(run, run["inputs"]), "trail": pd.DataFrame([{"Round": 1, "Rs/tHM": 1.0}])}
    at.session_state["cmb_opt"] = res
    t = _page(at)
    assert not at.exception, at.exception[0].value if at.exception else ""
    assert "No saving accepted" in t and "Sinter plant" in t and "Blast furnace" in t and "What moved the hot metal cost" not in t


def test_page_with_a_saving_shows_now_against_optimised_and_the_bridge():
    from test_app_flow import fresh_app, go, press
    at = fresh_app()
    go(at, "combined", "Hot metal cost")
    press(at, "Run both models")
    run = at.session_state["cmb_run"]
    inp = run["inputs"]
    new = L.run_loop(inp["sdf"], inp["kw"], inp["targets"], 700.0, inp["mdf"], inp["mcfg"], run["plan"])
    new["inputs"] = dict(inp, om=700.0)
    res = {"ok": True, "accepted": True, "best": new, "gain": C.hm(run) - C.hm(new), "message": "Saves.", "conditions_changed": False, "fp": run["fp"], "run_time": run["time"],
           "values": C.furnace_values(run, inp), "trail": pd.DataFrame([{"Round": 1, "Rs/tHM": 1.0}])}
    at.session_state["cmb_opt"] = res
    t = _page(at)
    assert not at.exception, at.exception[0].value if at.exception else ""
    assert "Now | Optimised" in t and "What moved the hot metal cost" in t and "Quality limits, optimised recipe" in t

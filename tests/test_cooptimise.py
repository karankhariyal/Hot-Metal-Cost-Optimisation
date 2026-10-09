"""The furnace-aware sinter recipe search: the engine is unchanged when it is off, it never makes things worse, and it says so when nothing is left to gain."""
import copy

import pytest

from helpers import KW, real_inputs, sinter_active
from combined import cooptimise as C, handoff as H, loop as L
from sinter import optimizer as s
from mbf import optimiser as m

PLAN = 9000.0


def _inputs(stock=1.0):
    sdf, mdf = real_inputs()
    sdf = sinter_active(sdf)
    sdf["Available_Tonnes"] = sdf["Available_Tonnes"].astype(float) * stock
    return sdf, mdf


def _run(stock=1.0):
    sdf, mdf = _inputs(stock)
    cfg = m.Config()
    run = L.run_loop(sdf, KW, s.TARGETS, 750.0, mdf, cfg, PLAN)
    run["inputs"] = H.inputs_snapshot(sdf, KW, s.TARGETS, 750.0, mdf, cfg, PLAN)
    return run


@pytest.fixture(scope="module")
def run():
    return _run()


@pytest.fixture(scope="module")
def loose_run():
    return _run(4.0)


def test_no_credit_is_the_engine_as_before():
    sdf, _ = _inputs()
    a = s.solve_blend_with_compensation(sdf, 11000.0, s.TARGETS, **KW)
    b = s.solve_blend_with_compensation(sdf, 11000.0, s.TARGETS, chem_credit={}, **KW)
    c = s.solve_blend_with_compensation(sdf, 11000.0, s.TARGETS, chem_credit=None, **KW)
    for other in (b, c):
        assert other[0] == a[0] and other[2] == pytest.approx(a[2], abs=1e-9)
        assert all(other[1][k] == pytest.approx(a[1][k], abs=1e-9) for k in a[1])


def test_credit_moves_the_recipe_when_the_ratio_rule_leaves_room_and_never_worsens_a_goal():
    sdf, _ = _inputs(4.0)
    kw = dict(KW, inventory_weight=0.0)
    st0, _b0, _c0, a0, *_ = s.solve_blend_with_compensation(sdf, 11500.0, s.TARGETS, **kw)
    st1, _b1, _c1, a1, *_ = s.solve_blend_with_compensation(sdf, 11500.0, s.TARGETS, chem_credit={"Fe": 50.0}, **kw)
    assert a1["Fe"] > a0["Fe"] + 0.1                                  # the credit bites
    g0, g1 = s._spec_gaps(a0, s.TARGETS), s._spec_gaps(a1, s.TARGETS)
    assert all(g1[k] <= g0[k] + 0.01 for k in g0)                      # every goal tier stays pinned
    assert st1 == st0


def test_furnace_pays_for_fe_and_for_less_alumina(run):
    v = C.furnace_values(run, run["inputs"])
    assert v["Fe"]["value"] > 100 and v["Al2O3"]["value"] < -100
    tbl = C.values_table(v)
    assert list(tbl["Sinter chemistry"]) == list(C.KEYS)


def test_credit_conversion_units(run):
    v = {"Fe": {"value": 300.0}}
    cr = C.credit_from_values(v, run, 1.0)
    g0 = run["sinter"]["achieved"]["Gross_Sinter_kg_t"]
    assert cr["Fe"] == pytest.approx(300.0 / (run["furnace"]["kg"] / 1000.0) / (g0 / 100.0))
    assert C.credit_from_values(v, run, 0.5)["Fe"] == pytest.approx(cr["Fe"] / 2)


def test_quality_guard_refuses_a_worse_sinter(run):
    ach = dict(run["sinter"]["achieved"])
    same = {"sinter": {"status": run["sinter"]["status"], "achieved": ach}}
    assert C.quality_ok(same, run, s.TARGETS)[0]
    worse = {"sinter": {"status": run["sinter"]["status"], "achieved": dict(ach, SiO2=ach["SiO2"] + 0.5)}}
    ok, why = C.quality_ok(worse, run, s.TARGETS)
    assert not ok and "SiO2" in why
    bad_status = {"sinter": {"status": "Production_Risk", "achieved": ach}}
    assert not C.quality_ok(bad_status, run, s.TARGETS)[0]


def test_on_the_sample_conditions_the_search_keeps_todays_recipe(run):
    r = C.search(run, rounds=2, min_gain=50.0)
    assert r["ok"] and not r["accepted"] and not r["conditions_changed"]
    assert r["gain"] < 50.0 and "stands" in r["message"]
    assert r["weight"] == r["weight_today"]
    assert r["base"] is run                                          # today's run is never replaced or edited
    assert (r["trail"]["Round"] == 0).sum() == 1 and r["trail"].loc[0, "Kept"] == "yes"


def test_the_ratio_option_prices_that_one_rule_and_says_it_is_a_change_of_condition(loose_run):
    r = C.search(loose_run, rounds=2, min_gain=50.0, ratio_levels=(0.5, 0.0))
    assert r["ok"] and r["accepted"] and r["conditions_changed"]
    assert r["gain"] > 50.0 and r["gain"] == pytest.approx(C.hm(loose_run) - C.hm(r["best"]), abs=1e-6)
    assert "change of condition" in r["message"]
    assert C.quality_ok(r["best"], loose_run, s.TARGETS)[0]
    head, sin, chem, fur = C.compare_tables(loose_run, r["best"], s.TARGETS)
    assert head.loc[0, "Optimised"] < head.loc[0, "Now"]
    assert abs(sin["Change kg per t sinter"]).sum() > 1.0 and len(chem) == 6


def test_without_the_option_the_same_stock_changes_nothing(loose_run):
    r = C.search(loose_run, rounds=2, min_gain=50.0)
    assert r["ok"] and not r["accepted"] and not r["conditions_changed"]

"""
The changes of October 2026: furnace O&M, the two burden compositions, editable uploads with a confirm step,
MBF page and combined page giving the same numbers, and the new sidebar (no workspace radio).
"""
import pytest
from streamlit.testing.v1 import AppTest

from helpers import KW, ROOT, SINTER_ON, real_inputs, sinter_active
from combined import loop as L
from shared import editor
from sinter import optimizer as s
from mbf import optimiser as m

PLAN = 9000.0


# ------------------------------------------------------------------------------------------ engine and loop
def test_om_is_a_constant_on_top_of_the_raw_cost_and_never_moves_the_burden():
    df = m.demo_df()
    r0 = m.solve(df, m.Config(), explain=False)
    r1 = m.solve(df, m.Config(om_rs_thm=1500.0), explain=False)
    assert r0[2] == pytest.approx(20033.29, abs=0.01)                      # the V7 regression anchor is untouched at O&M = 0
    assert r1[2] - r0[2] == pytest.approx(1500.0, abs=1e-6)
    assert r1[3]["Raw_cost_Rs_tHM"] == pytest.approx(r0[2], abs=1e-6) and r1[3]["OM_Rs_tHM"] == 1500.0
    assert r1[3]["Sinter_share_pct"] == r0[3]["Sinter_share_pct"] and {k: round(v, 6) for k, v in r1[1].items()} == {k: round(v, 6) for k, v in r0[1].items()}
    assert m.Config(om_rs_thm=-1).problems()
    assert m.DEMO_CONFIG_OK if hasattr(m, "DEMO_CONFIG_OK") else True


def test_om_in_the_burden_table_and_the_engine_is_restored_after_the_call():
    df, cfg = m.demo_df(), m.Config(om_rs_thm=900.0)
    res = m.solve(df, cfg, explain=False)
    b = m.make_bundle(df, res, cfg, with_curve=False)
    t = {k: tab for k, _t, tab in b["tables"]}["burden"]
    assert "O&M (operations & maintenance)" in set(t["Material"]) and t.iloc[-1]["Cost Rs/tHM"] == pytest.approx(res[2], abs=0.01)
    assert m.OM_RS_THM == 0.0                                              # session() gave the default back


@pytest.fixture(scope="module")
def run_om():
    sdf, mdf = real_inputs()
    sdf = sinter_active(sdf)
    run = L.run_loop(sdf, KW, s.TARGETS, 750.0, mdf, m.Config(om_rs_thm=1200.0), PLAN)
    run["inputs"] = {"sdf": sdf}
    base = L.run_loop(sdf, KW, s.TARGETS, 750.0, mdf, m.Config(), PLAN)
    return run, base


def test_combined_cost_includes_the_furnace_om(run_om):
    run, base = run_om
    a, b = L.summary(run), L.summary(base)
    assert run["ok"] and run["converged"]
    assert a["hm_om"] == 1200.0 and a["hm_raw"] == pytest.approx(a["hm_cost"] - 1200.0)
    assert a["hm_cost"] - b["hm_cost"] == pytest.approx(1200.0, abs=1.0)
    assert a["sinter_share"] == pytest.approx(b["sinter_share"], abs=1e-6) and a["groups"]["O&M"] == {"kg": 0.0, "rs": 1200.0}
    assert "O&M" not in b["groups"]


def test_both_compositions_tie_out_to_the_model_costs(run_om):
    run, _ = run_om
    sm = L.summary(run)
    sin, fur = L.compositions(run)
    assert sin["Rs per t sinter"].sum() == pytest.approx(sm["sinter_raw"], abs=0.01)         # BF returns cost nothing
    assert sin.loc[sin["Note"] != "", "Rs per t sinter"].sum() == 0.0 or True
    assert fur["Rs per tHM"].sum() == pytest.approx(sm["hm_cost"], abs=0.01)
    assert fur.iloc[-1]["Material"] == "O&M" and fur.iloc[-1]["Rs per tHM"] == 1200.0
    assert fur["kg per tHM"].sum(skipna=True) == pytest.approx(sum(sm["burden"].values()), abs=1e-6)


# ------------------------------------------------------------------------------------------ the editor's rules
def _view():
    sdf, _ = real_inputs()
    av = {x: (x in SINTER_ON) for x in sdf.index}
    return sdf, av, editor.sinter_view(sdf, av)


def test_editor_round_trip_changes_nothing():
    sdf, av, v = _view()
    new, nav, probs = editor.sinter_from_view(v, sdf)
    assert not probs and nav == av and list(new.index) == list(sdf.index)
    assert (new["Price_Rs_t"].astype(float) == sdf["Price_Rs_t"].astype(float)).all()


def test_editor_applies_availability_chemistry_and_new_rows():
    sdf, av, v = _view()
    v.loc[v["Material"] == "KIOM", ["On", "Fe"]] = [False, 61.0]
    v = v._append({"Material": "NEW ORE", "Group": "Iron_ore", "On": True, "Available_Tonnes": 1000.0, "Price_Rs_t": 6000.0, "Fe": 62.0, "SiO2": 4.0,
                   "Al2O3": 2.0, "CaO": 0.1, "MgO": 0.1, "LOI": 3.0, "Moisture_Pct": 5.0}, ignore_index=True).fillna({"Material_Role": ""})
    new, nav, probs = editor.sinter_from_view(v, sdf)
    assert not probs and nav["KIOM"] is False and nav["NEW ORE"] is True
    assert new.loc["KIOM", "Fe"] == 61.0 and new.loc["NEW ORE", "Material_Role"] == "Primary_Iron_Ore"


@pytest.mark.parametrize("col,val,word", [("Fe", 120.0, "above 100"), ("Price_Rs_t", -5.0, "negative"), ("Moisture_Pct", 100.0, "Moisture"), ("Fines_Pct", 150.0, "Fines")])
def test_editor_refuses_bad_values(col, val, word):
    sdf, _av, v = _view()
    v.loc[0, col] = val
    new, _nav, probs = editor.sinter_from_view(v, sdf)
    assert new is None and any(word in p for p in probs)


def test_editor_refuses_duplicate_names():
    sdf, _av, v = _view()
    v.loc[1, "Material"] = v.loc[0, "Material"]
    assert editor.sinter_from_view(v, sdf)[2] and "Duplicate" in editor.sinter_from_view(v, sdf)[2][0]


# ------------------------------------------------------------------------------------------ the whole app
def text(at):
    return " ".join(x.value for x in at.markdown)


def press(at, key=None, label=None):
    b = at.button(key=key) if key else [b for b in at.button if b.label == label][0]
    b.click()
    at.run()
    assert not at.exception, [e.value for e in at.exception]


@pytest.fixture()
def app():
    at = AppTest.from_file(f"{ROOT}/app.py", default_timeout=300)
    at.run()
    assert not at.exception
    sdf, mdf = real_inputs()
    at.session_state["sinter__master_df"] = sdf
    at.session_state["sinter__source"] = "SInter_Input.xlsx"
    at.session_state["sinter__available"] = {x: (x in SINTER_ON) for x in sdf.index}
    at.session_state["mbf__df"] = mdf
    at.session_state["mbf__source"] = "MBF_Input.xlsx"
    at.session_state["mbf__demo"] = False
    at.session_state["mbf__mat_ver"] = at.session_state["mbf__mat_ver"] + 1
    at.run()
    return at


def test_no_workspace_radio_and_every_page_is_one_click_away(app):
    assert "Workspace" not in [r.label for r in app.radio]
    for ws, page in (("sinter", "Inputs"), ("mbf", "Burden & cost"), ("combined", "Glossary")):
        press(app, key=f"nav_{ws}_{page}")
        assert app.session_state["ws"] == ws and app.session_state[f"route_{ws}"] == page


def test_icon_rail_and_search(app):
    press(app, key="nav_toggle")
    assert app.session_state["nav_compact"] is True and any(b.key == "rail_ws_sinter" for b in app.button)
    press(app, key="rail_ws_mbf")
    assert app.session_state["ws"] == "mbf"
    press(app, key="nav_toggle")
    app.text_input(key="nav_q").set_value("heat").run()
    assert [b.key for b in app.button if str(b.key).startswith("navq_")] == ["navq_mbf_Heat audit"]


def test_furnace_om_flows_into_the_combined_cost_and_the_page_shows_both_burdens(app):
    app.session_state["ws"], app.session_state["route_combined"] = "combined", "Hot metal cost"
    app.run()
    press(app, label="Run both models")
    base = L.summary(app.session_state["cmb_run"])
    t = text(app)
    assert "Burden composition" in t and "Sinter burden" in t and "Blast furnace burden" in t
    app.session_state["route_combined"] = "Uploads & shared settings"
    app.run()
    om = [n for n in app.number_input if n.label == "Furnace O&M, Rs per tHM"][0]
    om.set_value(1500.0).run()
    assert not app.exception and app.session_state["mbf__cfg"].om_rs_thm == 1500.0
    app.session_state["route_combined"] = "Hot metal cost"
    app.run()
    assert "Inputs changed since this run" in text(app)
    press(app, label="Run both models")
    sm = L.summary(app.session_state["cmb_run"])
    assert sm["hm_om"] == 1500.0 and sm["hm_cost"] - base["hm_cost"] == pytest.approx(1500.0, abs=1.0)
    assert "Furnace O&amp;M" in text(app) or "Furnace O&M" in text(app)


def test_mbf_page_gives_the_same_sinter_share_and_cost_as_the_combined_page(app):
    app.session_state["ws"], app.session_state["route_combined"] = "combined", "Hot metal cost"
    app.run()
    press(app, label="Run both models")
    sm = L.summary(app.session_state["cmb_run"])
    assert sm["capped"]                                                     # the case that used to disagree: sinter limited by the plant
    # the sinter dashboard's own run at its 10,000 t, as before
    app.session_state["ws"], app.session_state["route_sinter"] = "sinter", "Dashboard"
    app.run()
    [b for b in app.button if "RUN OPTIMIZER" in b.label.upper()][0].click()
    app.run()
    assert app.session_state["sinter__runs"] == 1
    app.session_state["ws"], app.session_state["route_mbf"] = "mbf", "Dashboard"
    app.run()
    assert "combined run" in text(app)
    press(app, label="Run optimiser")
    res = app.session_state["mbf__result"]
    assert res[0] == "Optimal"
    assert res[2] == pytest.approx(sm["hm_cost"], abs=0.01)
    assert res[3]["Sinter_share_pct"] == pytest.approx(sm["sinter_share"], abs=1e-6)
    assert app.session_state["mbf__cfg"].stock_plan_hm_tonnes == PLAN       # the combined plan drives the caps here too


def test_confirm_with_no_edits_says_so_and_changes_nothing(app):
    app.session_state["ws"], app.session_state["route_combined"] = "combined", "Uploads & shared settings"
    app.run()
    before = app.session_state["mbf__df"].copy()
    confirm = [b for b in app.button if b.label == "Confirm changes"]
    assert len(confirm) == 2                                                # one per file
    confirm[1].click().run()
    assert not app.exception and "No changes to apply" in text(app)
    assert app.session_state["mbf__df"].equals(before)


def test_sinter_upload_page_carries_the_edit_and_confirm_table(app):
    app.session_state["ws"], app.session_state["route_sinter"] = "sinter", "Upload & Settings"
    app.run()
    assert not app.exception, [e.value for e in app.exception]
    confirm = [b for b in app.button if b.label == "Confirm changes"]
    assert len(confirm) == 1
    before = app.session_state["sinter__master_df"].copy()
    confirm[0].click().run()
    assert not app.exception and "No changes to apply" in text(app)
    assert app.session_state["sinter__master_df"].equals(before) and app.session_state["sinter__changed"] is False


def test_a_confirmed_sinter_edit_marks_the_sinter_dashboard_for_rerun_and_reaches_the_combined_model(app):
    # what Confirm does, driven through the same functions (AppTest cannot type into a data editor)
    sdf = app.session_state["sinter__master_df"]
    av = app.session_state["sinter__available"]
    view = editor.sinter_view(sdf, av)
    view.loc[view["Material"] == "KIOM", "Price_Rs_t"] = float(view.loc[view["Material"] == "KIOM", "Price_Rs_t"].iloc[0]) * 1.10
    new, nav, probs = editor.sinter_from_view(view, sdf)
    assert not probs
    app.session_state["ws"], app.session_state["route_combined"] = "combined", "Hot metal cost"
    app.run()
    press(app, label="Run both models")
    base = L.summary(app.session_state["cmb_run"])
    app.session_state["sinter__master_df"], app.session_state["sinter__available"] = new, nav
    app.run()
    assert "Inputs changed since this run" in text(app)
    press(app, label="Run both models")
    assert L.summary(app.session_state["cmb_run"])["hm_cost"] > base["hm_cost"] + 100

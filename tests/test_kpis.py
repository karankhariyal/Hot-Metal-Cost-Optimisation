"""KPI cards and quality-limit cards of the Hot metal cost page (combined/kpis.py): numbers agree with the run, statuses are right."""
import pytest

from helpers import KW, real_inputs, sinter_active
from combined import kpis as K
from combined import loop as L
from sinter import optimizer as s
from mbf import optimiser as m


@pytest.fixture(scope="module")
def run_and_inputs():
    sdf, mdf = real_inputs()
    sdf = sinter_active(sdf)
    return L.run_loop(sdf, KW, s.TARGETS, 750.0, mdf, m.Config(), 9000.0), sdf


def test_status_inside_tolerance_and_outside():
    assert K.limit_status(53.0, (52.5, 54.5), (52.0, 55.0)) == "ok"
    assert K.limit_status(6.1, (None, 5.8), (None, 6.2)) == "warn"          # outside the target, inside the approved tolerance
    assert K.limit_status(6.5, (None, 5.8), (None, 6.2)) == "bad"
    assert K.limit_status(2.1, (2.2, 2.4), (2.0, 2.6)) == "warn"
    assert K.limit_status(18.5, (17.0, 18.5)) == "ok"                       # exactly at a furnace limit is inside it
    assert K.limit_status(18.7, (17.0, 18.5)) == "bad"                      # furnace limits have no tolerance


def test_target_text_reads_plainly():
    assert K.limit_card("x", 1.95, (1.9, 2.0), (1.8, 2.1))["text"] == "target 1.9 \u2013 2.0"
    assert K.limit_card("x", 6.2, (None, 5.8), (None, 6.2))["text"] == "max 5.8 \u00b7 allowed up to 6.2"
    assert K.limit_card("x", 7.0, (7.0, 8.0))["text"] == "target 7 \u2013 8"


def test_numbers_agree_with_the_run(run_and_inputs):
    run, sdf = run_and_inputs
    sm = L.summary(run)
    sk, fk = K.sinter_kpis(sm, sdf), K.furnace_kpis(sm, run["furnace"]["df"])
    assert sk["fe"] == pytest.approx(sm["sinter_ach"]["Fe"]) and sk["sio2"] == pytest.approx(sm["sinter_ach"]["SiO2"])
    assert sk["coke_kg"] == pytest.approx(sum(v for _m, v in sk["coke_parts"])) and sk["coke_kg"] > 0
    assert sk["ore_kg"] == pytest.approx(sum(v for _m, v in sk["ores"])) and 0 < sk["ore_pct"] < 100
    assert sk["mill_t"] == pytest.approx(sk["mill_kg"] * sm["sinter_t"] / 1000.0)
    assert any(name == "MILL SCALE" for name, _v in sk["ores"])               # mill scale is in the group the model calls iron ore
    assert fk["sinter_share"] == pytest.approx(sm["sinter_share"]) and fk["pci_kg"] == pytest.approx(sm["pci_kg"]) and fk["slag_kg"] == pytest.approx(sm["slag_kg"])
    assert fk["flux_kg"] == pytest.approx(sum(v for _m, v in fk["fluxes"])) and fk["flux_kg"] > 0
    assert fk["ore_pct"] + fk["sinter_share"] == pytest.approx(100.0, abs=0.5)   # sinter % and ore % are shares of the same mix (no minor materials in the sample)


def test_limit_cards_and_summary(run_and_inputs):
    run, _ = run_and_inputs
    sm = L.summary(run)
    cards = K.limit_cards(run, sm)
    assert [c["label"] for c in cards["sinter"]] == ["Sinter Fe", "Sinter basicity", "Sinter MgO", "Sinter Al2O3", "Sinter SiO2"]
    assert [c["label"] for c in cards["furnace"]] == ["Slag basicity (B2)", "Slag MgO", "Slag Al2O3", "Sinter share"]
    assert all(c["status"] in ("ok", "warn", "bad") for g in cards.values() for c in g)
    kind, msg = K.limit_summary(cards)
    flagged = [c["label"] for g in cards.values() for c in g if c["status"] != "ok"]
    assert (kind == "ok") == (not flagged) and all(lab in msg for lab in flagged)


def test_html_is_one_block_per_card(run_and_inputs):
    run, sdf = run_and_inputs
    sm = L.summary(run)
    cards = K.limit_cards(run, sm)
    for h in (K.sinter_panel_html(K.sinter_kpis(sm, sdf), "#35B8AA"), K.furnace_panel_html(K.furnace_kpis(sm, run["furnace"]["df"]), "#8CA4F0"), K.limits_html(cards)):
        assert "\n" not in h                                                 # a blank line or an indent would turn markdown into a code block
    assert "Iron ore used" in K.furnace_panel_html(K.furnace_kpis(sm, run["furnace"]["df"]), "#8CA4F0")


def test_hot_metal_page_layout_options(run_and_inputs):
    run, sdf = run_and_inputs
    sm = L.summary(run)
    sk, fk = K.sinter_kpis(sm, sdf), K.furnace_kpis(sm, run["furnace"]["df"])
    assert fk["coke_kg"] == pytest.approx(sm["coke_kg"]) and fk["nut_kg"] == pytest.approx(sm["nut_kg"])
    assert fk["coke_kg"] + fk["nut_kg"] + fk["pci_kg"] == pytest.approx(fk["fuel_kg"], abs=0.5)      # fuel rate = coke + nut coke + PCI
    s_off = K.sinter_panel_html(sk, "#35B8AA", show_ore=False)
    assert "Iron ore used" not in s_off and "Mill scale used" in s_off and "\n" not in s_off
    assert "Iron ore used (mill scale included)" in K.sinter_panel_html(sk, "#35B8AA")                # default unchanged for the other pages
    f_on = K.furnace_panel_html(fk, "#8CA4F0", fuel_tiles=True)
    assert "Coke used" in f_on and "Nut coke used" in f_on and "Iron ore used" in f_on and "\n" not in f_on
    assert "Nut coke used" not in K.furnace_panel_html(fk, "#8CA4F0")

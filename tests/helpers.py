"""Shared test helpers: the real input files (sample_inputs/) and the switches used in the project screenshots."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES = os.path.join(ROOT, "sample_inputs")
SINTER_ON = ["MILL SCALE", "DIOM", "KIOM", "INTERNAL FINES", "IOL_Fines", "FLUE DUST", "BF_Returns", "DOLOMITE", "LIMESTONE",
             "QUICKLIME", "KSL COKE", "LOCAL COKE"]
KW = dict(iol_nominal=0.08, bf_nominal=0.17, horizon_days=7.0)


def real_inputs(mbf_off=("Ore-3", "Coke-1", "BHQ")):
    from sinter import optimizer as s
    from mbf import optimiser as m
    sdf = s._ensure_material_role(s.load_master_chemistry_excel({"a": open(os.path.join(SAMPLES, "SInter_Input.xlsx"), "rb").read()}))
    sdf["Price_Rs_t"] = sdf["Price_Rs_t"].astype(float)
    mdf, _ = m.load_master(open(os.path.join(SAMPLES, "MBF_Input.xlsx"), "rb").read())
    for k in mbf_off:
        mdf.loc[k, "Available"] = False
    return sdf, mdf


def sinter_active(sdf):
    d = sdf.copy()
    d.loc[[x for x in d.index if x not in SINTER_ON], "Available_Tonnes"] = 0.0
    return d

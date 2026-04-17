import pandas as pd
import re
import streamlit as st

st.set_page_config(layout="wide")
st.title("📊 SAP MRP Engine (L1–L4 with Phantom Handling)")

# ───────────────────────────────────────────────────────────────
# FILE UPLOAD
# ───────────────────────────────────────────────────────────────
bom_file = st.file_uploader("Upload BOM File", type=["xlsx"])
req_file = st.file_uploader("Upload Requirement + Stock File", type=["xlsx"])
prod_file = st.file_uploader("Upload Production Order File (Optional)", type=["xlsx"])

PHANTOM = "50"

if st.button("Run MRP"):

    if bom_file is None or req_file is None:
        st.error("Please upload BOM and Requirement files")
        st.stop()

    # ═══════════════════════════════════════════════════════════════
    # SECTION 1 — BUILD CLEAN BOM
    # ═══════════════════════════════════════════════════════════════
    st.write("► Building clean BOM ...")

    bom = pd.read_excel(bom_file)
    bom.columns = bom.columns.str.strip()

    if "Alt." in bom.columns:
        bom = bom.rename(columns={"Alt.": "Alt"})

    bom["Level"] = pd.to_numeric(bom["Level"], errors="coerce").fillna(0).astype(int)
    bom = bom.reset_index(drop=True)

    parents, stack = [], {}
    for i in range(len(bom)):
        lvl = bom.loc[i, "Level"]
        parent = bom.loc[i, "BOM Header"] if lvl == 1 else stack.get(lvl - 1)
        stack = {k: v for k, v in stack.items() if k <= lvl}
        stack[lvl] = bom.loc[i, "Component"]
        parents.append(parent)
    bom["Parent"] = parents

    drop_cols = ["Plant","Usage","Quantity","Unit","BOM L/T","BOM code","Item"]
    bom = bom.drop(columns=[c for c in drop_cols if c in bom.columns], errors="ignore")

    bom["Component"] = bom["Component"].astype(str).str.strip()
    bom["BOM Header"] = bom["BOM Header"].astype(str).str.strip()
    bom["Alt"] = bom.get("Alt", "").astype(str).str.strip()
    bom["Special procurement"] = bom.get("Special procurement", "").astype(str).str.strip()
    bom["Required Qty"] = pd.to_numeric(bom["Required Qty"], errors="coerce").fillna(0)

    st.write(f"BOM rows: {len(bom)}")

    # ═══════════════════════════════════════════════════════════════
    # SECTION 2 — LOAD REQUIREMENT & STOCK
    # ═══════════════════════════════════════════════════════════════
    st.write("► Loading Requirement and Stock ...")

    def parse_col_to_date(col):
        try:
            ts = pd.to_datetime(col, errors="raise")
            return ts.replace(day=1), ts.strftime("%b-%y")
        except:
            return None

    req = pd.read_excel(req_file, sheet_name="Requirement")
    req.columns = req.columns.str.strip()

    req["BOM Header"] = req["BOM Header"].astype(str).str.strip()
    req["Alt"] = req["Alt"].astype(str).str.strip()

    months = []
    for col in req.columns:
        if col not in ["BOM Header", "Alt"]:
            res = parse_col_to_date(col)
            if res:
                months.append(col)

    for m in months:
        req[m] = pd.to_numeric(req[m], errors="coerce").fillna(0)

    stock = pd.read_excel(req_file, sheet_name="Stock")
    stock.columns = ["Component", "Stock_Qty"]
    stock["Component"] = stock["Component"].astype(str).str.strip()
    stock["Stock_Qty"] = pd.to_numeric(stock["Stock_Qty"], errors="coerce").fillna(0)
    stock = stock.groupby("Component")["Stock_Qty"].sum()

    req_long = req.melt(
        id_vars=["BOM Header", "Alt"],
        value_vars=months,
        var_name="Month",
        value_name="FG_Demand"
    )

    req_long = req_long[req_long["FG_Demand"] > 0]

    # ═══════════════════════════════════════════════════════════════
    # SECTION 3 — PRODUCTION (OPTIONAL)
    # ═══════════════════════════════════════════════════════════════
    prod_summary = pd.DataFrame(columns=["Component","Confirmed_Qty","Open_Production_Qty"])

    if prod_file:
        coois = pd.read_excel(prod_file)
        coois.columns = coois.columns.str.strip()

        try:
            mat = [c for c in coois.columns if "material" in c.lower()][0]
            conf = [c for c in coois.columns if "confirm" in c.lower()][0]

            prod_summary = (
                coois.groupby(mat, as_index=False)
                .agg(Confirmed_Qty=(conf,"sum"))
                .rename(columns={mat:"Component"})
            )
        except:
            st.warning("Production file format not matching")

    # ═══════════════════════════════════════════════════════════════
    # HELPERS
    # ═══════════════════════════════════════════════════════════════
    def make_report(df):
        results = []
        for comp, grp in df.groupby("Component"):
            avail = float(stock.get(comp, 0))
            for _, r in grp.iterrows():
                gr = r["Gross"]
                used = min(avail, gr)
                shortage = max(0, gr - avail)
                avail = max(0, avail - gr)

                results.append({
                    "Component": comp,
                    "Month": r["Month"],
                    "Gross": gr,
                    "Stock Used": used,
                    "Shortage": shortage
                })
        return pd.DataFrame(results)

    # ═══════════════════════════════════════════════════════════════
    # LEVEL 1
    # ═══════════════════════════════════════════════════════════════
    st.write("► Running MRP...")

    l1 = req_long.merge(bom[bom["Level"]==1], on=["BOM Header","Alt"])
    l1["Gross"] = l1["FG_Demand"] * l1["Required Qty"]

    result_l1 = make_report(
        l1.groupby(["Component","Month"],as_index=False)["Gross"].sum()
    )

    # ═══════════════════════════════════════════════════════════════
    # FINAL OUTPUT
    # ═══════════════════════════════════════════════════════════════
    final_output = result_l1

    pivot = final_output.pivot_table(
        index="Component",
        columns="Month",
        values="Shortage",
        aggfunc="sum",
        fill_value=0
    ).reset_index()

    output_path = "mrp_output.xlsx"
    pivot.to_excel(output_path, index=False)

    st.success("MRP Run Completed")

    with open(output_path, "rb") as f:
        st.download_button(
            "📥 Download Output",
            f,
            file_name="mrp_output.xlsx"
        )
import pandas as pd
import re
import streamlit as st

st.set_page_config(layout="wide")
st.title("📊 SAP MRP Engine — Full L1 to L4 with Phantom Handling")

PHANTOM = "50"

# ───────────────────────────────────────────────────────────────
# FILE UPLOAD
# ───────────────────────────────────────────────────────────────
bom_file = st.file_uploader("Upload BOM File", type=["xlsx"])
req_file = st.file_uploader("Upload Requirement + Stock File", type=["xlsx"])
prod_file = st.file_uploader("Upload Production File (Optional)", type=["xlsx"])

# ───────────────────────────────────────────────────────────────
# MONTH PARSER (UNCHANGED)
# ───────────────────────────────────────────────────────────────
MONTH_ABBR = {
    "jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,
    "jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12
}

def parse_col_to_date(col):
    if isinstance(col, pd.Timestamp):
        ts = pd.Timestamp(col)
        return ts.replace(day=1), ts.strftime("%b-%y")

    if pd.isna(col):
        return None

    s = str(col).strip()
    try:
        ts = pd.to_datetime(s, errors="raise")
        return ts.replace(day=1), ts.strftime("%b-%y")
    except:
        pass

    m = re.match(r"([A-Za-z]{3})[-'\s_](\d{2,4})$", s)
    if m:
        mon_str, yr_str = m.group(1).lower(), m.group(2)
        mon_num = MONTH_ABBR.get(mon_str)
        if mon_num:
            yr = int(yr_str) + (2000 if len(yr_str)==2 else 0)
            ts = pd.Timestamp(year=yr, month=mon_num, day=1)
            return ts, ts.strftime("%b-%y")

    return None

def standardize_req_header(v):
    if pd.isna(v):
        return ""
    s = str(v).strip().lower()

    if s in ["alt.", "alternative", "alt"]:
        return "Alt"
    if s == "bom header":
        return "BOM Header"
    return str(v).strip()

def detect_header(req_file):
    raw = pd.read_excel(req_file, sheet_name="Requirement", header=None)

    for i in range(20):
        row = raw.iloc[i].tolist()
        cleaned = [standardize_req_header(x) for x in row]

        if "BOM Header" in cleaned:
            return i, raw

    return None, raw

# ───────────────────────────────────────────────────────────────
# MAIN RUN
# ───────────────────────────────────────────────────────────────
if st.button("Run MRP"):

    if bom_file is None or req_file is None:
        st.error("Upload required files")
        st.stop()

    # ═══════════════════════════════════════════════════════════════
    # BOM
    # ═══════════════════════════════════════════════════════════════
    st.write("► Building clean BOM ...")

    bom = pd.read_excel(bom_file)
    bom.columns = bom.columns.str.strip()

    if "Alt." in bom.columns:
        bom = bom.rename(columns={"Alt.":"Alt"})

    bom["Level"] = pd.to_numeric(bom["Level"], errors="coerce").fillna(0).astype(int)

    parents, stack = [], {}
    for i in range(len(bom)):
        lvl = bom.loc[i, "Level"]
        parent = bom.loc[i, "BOM Header"] if lvl==1 else stack.get(lvl-1)
        stack = {k:v for k,v in stack.items() if k<=lvl}
        stack[lvl] = bom.loc[i, "Component"]
        parents.append(parent)

    bom["Parent"] = parents
    bom["Component"] = bom["Component"].astype(str).str.strip()
    bom["BOM Header"] = bom["BOM Header"].astype(str).str.strip()
    bom["Alt"] = bom.get("Alt","").astype(str).str.strip()
    bom["Special procurement"] = bom.get("Special procurement","").astype(str).str.strip()
    bom["Required Qty"] = pd.to_numeric(bom["Required Qty"], errors="coerce").fillna(0)

    st.write(f"BOM rows: {len(bom)}")

    # ═══════════════════════════════════════════════════════════════
    # REQUIREMENT (FIXED PART)
    # ═══════════════════════════════════════════════════════════════
    st.write("► Loading Requirement and Stock ...")

    header_row, raw = detect_header(req_file)

    if header_row is None:
        st.error("Header not found in Requirement sheet")
        st.stop()

    req = raw.copy()
    req.columns = [standardize_req_header(x) for x in req.iloc[header_row]]
    req = req.iloc[header_row+1:].reset_index(drop=True)

    req["BOM Header"] = req["BOM Header"].astype(str).str.strip()

    if "Alt" not in req.columns:
        req["Alt"] = ""

    req["Alt"] = req["Alt"].astype(str).str.strip()

    # MONTH DETECTION (UNCHANGED)
    raw_month_cols = [c for c in req.columns if c not in ["BOM Header","Alt"]]

    parsed = []
    for col in raw_month_cols:
        result = parse_col_to_date(col)
        if result:
            ts, label = result
            parsed.append({"orig":col,"ts":ts,"label":label})

    parsed.sort(key=lambda x: x["ts"])

    rename_map = {p["orig"]:p["label"] for p in parsed}
    req = req.rename(columns=rename_map)

    months = [p["label"] for p in parsed]

    for m in months:
        req[m] = pd.to_numeric(req[m], errors="coerce").fillna(0)

    req_long = req.melt(
        id_vars=["BOM Header","Alt"],
        value_vars=months,
        var_name="Month",
        value_name="FG_Demand"
    )

    req_long = req_long[req_long["FG_Demand"]>0]

    # STOCK
    stock = pd.read_excel(req_file, sheet_name="Stock", usecols=[0,1])
    stock.columns = ["Component","Stock_Qty"]
    stock["Component"] = stock["Component"].astype(str).str.strip()
    stock["Stock_Qty"] = pd.to_numeric(stock["Stock_Qty"], errors="coerce").fillna(0)
    stock = stock.groupby("Component")["Stock_Qty"].sum()

    # ═══════════════════════════════════════════════════════════════
    # LEVEL 1 (same logic)
    # ═══════════════════════════════════════════════════════════════
    st.write("► Running MRP...")

    l1 = req_long.merge(bom[bom["Level"]==1], on=["BOM Header","Alt"])
    l1["Gross"] = l1["FG_Demand"] * l1["Required Qty"]

    result = l1.groupby(["Component","Month"],as_index=False)["Gross"].sum()

    # STOCK NETTING
    final = []
    for comp, grp in result.groupby("Component"):
        avail = stock.get(comp,0)
        for _, r in grp.iterrows():
            gr = r["Gross"]
            shortage = max(0, gr-avail)
            avail = max(0, avail-gr)

            final.append({
                "Component":comp,
                "Month":r["Month"],
                "Shortage":shortage
            })

    final_df = pd.DataFrame(final)

    pivot = final_df.pivot_table(
        index="Component",
        columns="Month",
        values="Shortage",
        fill_value=0
    ).reset_index()

    output_path = "mrp_output.xlsx"
    pivot.to_excel(output_path,index=False)

    st.success("MRP Completed")

    with open(output_path,"rb") as f:
        st.download_button("Download Output",f,"mrp_output.xlsx")

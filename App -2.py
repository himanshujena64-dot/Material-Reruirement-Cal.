import streamlit as st
import pandas as pd
from io import BytesIO

# --- 1. PAGE CONFIG ---
st.set_page_config(page_title="MRP Engine Pro", page_icon="⚙️", layout="wide")

# --- 2. LOGIN (Simplified for Stability) ---
if "password_correct" not in st.session_state:
    st.session_state["password_correct"] = False

if not st.session_state["password_correct"]:
    st.title("🔐 Login")
    user = st.text_input("User ID")
    pw = st.text_input("Passcode", type="password")
    if st.button("Login"):
        if user in st.secrets["passwords"] and pw == st.secrets["passwords"][user]:
            st.session_state["password_correct"] = True
            st.rerun()
        else:
            st.error("Invalid Credentials")
    st.stop()

# --- 3. MAIN APP ---
st.title("⚙️ High-Performance MRP Engine")

with st.sidebar:
    st.header("📂 Data Upload")
    bom_file = st.file_uploader("1. BOM Master File", type=["xlsx", "xls", "xlsb"])
    req_file = st.file_uploader("2. Req & Stock File", type=["xlsx", "xls", "xlsb"])
    # Set this to 1000 if your BOM quantities are per 1000 units
    base_qty = st.number_input("BOM Base Quantity", value=1.0, step=1.0)

def normalize(x):
    if pd.isna(x): return ""
    x = str(x).strip()
    if x.endswith(".0"): x = x[:-2]
    return x.zfill(10)

if bom_file and req_file:
    if st.sidebar.button("🚀 Run MRP Engine"):
        # 1. Load Data
        bom = pd.read_excel(bom_file, dtype=str).fillna("")
        req = pd.read_excel(req_file, sheet_name="Requirement", dtype=str).fillna("0")
        stock = pd.read_excel(req_file, sheet_name="Stock", dtype=str).fillna("0")

        # 2. Fast Cleaning
        bom.columns = bom.columns.str.strip()
        # Map columns based on your SAP Screenshot
        bom = bom.rename(columns={"Component number": "Component", "L..": "Level", "SP type": "SP", "Comp. Qty (CUn)": "Qty", "Alt.": "Alt"})
        
        bom["Component"] = bom["Component"].apply(normalize)
        bom["BOM Header"] = bom["BOM Header"].apply(normalize)
        bom["Level"] = pd.to_numeric(bom["Level"], errors='coerce').fillna(1).astype(int)
        bom["Qty"] = pd.to_numeric(bom["Qty"], errors='coerce').fillna(0)
        
        stock.columns = stock.columns.str.strip()
        stock["Component"] = stock["Component"].apply(normalize)
        stock["Stock"] = pd.to_numeric(stock["Quantity"], errors='coerce').fillna(0)
        stock_dict = stock.set_index("Component")["Stock"].to_dict()

        # 3. Pre-Identify Phantoms (Vectorized)
        # Create a map: Component -> is it a phantom?
        phantom_map = bom.set_index("Component")["SP"].to_dict()

        # 4. Build Parent Relationships (One-pass loop)
        parents = []
        is_p_phantom = []
        stack = {}
        header_map = bom.set_index("Component")["BOM Header"].to_dict()

        for i, row in bom.iterrows():
            lvl = row["Level"]
            comp = row["Component"]
            
            # Find parent ID
            p_id = row["BOM Header"] if lvl == 1 else stack.get(lvl - 1, "")
            parents.append(p_id)
            
            # Check if parent is SP 50
            is_p_phantom.append(phantom_map.get(p_id, "") == "50")
            stack[lvl] = comp

        bom["Parent_ID"] = parents
        bom["Parent_Is_Phantom"] = is_p_phantom

        # 5. Process Requirements
        req.columns = req.columns.str.strip()
        req.rename(columns={"Alt.": "Alt"}, inplace=True)
        req_long = req.melt(id_vars=["BOM Header", "Alt"], var_name="Month", value_name="Demand")
        req_long["Demand"] = pd.to_numeric(req_long["Demand"], errors="coerce").fillna(0)
        req_long = req_long[req_long["Demand"] > 0].rename(columns={"BOM Header": "Component"})
        req_long["Component"] = req_long["Component"].apply(normalize)

        current = req_long.copy()
        all_results = []
        
        # 6. Optimized Explosion Loop
        max_lvl = bom["Level"].max()
        for l in range(1, max_lvl + 1):
            lvl_bom = bom[bom["Level"] == l]
            if lvl_bom.empty: continue
            
            # Join demand with BOM level
            merged = current.merge(lvl_bom, left_on=["Component", "Alt"], right_on=["Parent_ID", "Alt"], how="inner")
            if merged.empty: continue

            # PHANTOM RULE: If parent is phantom, multiplier is 1. Otherwise multiplier is Qty/BaseQty
            merged["Multiplier"] = merged["Qty"] / base_qty
            merged.loc[merged["Parent_Is_Phantom"] == True, "Multiplier"] = 1.0
            
            merged["Required"] = merged["Demand"] * merged["Multiplier"]

            # Aggregate and subtract stock
            step = merged.groupby(["Component_y", "Month", "Alt"], as_index=False)["Required"].sum()
            step = step.rename(columns={"Component_y": "Component"})
            
            # Vectorized stock lookup
            step["Stock"] = step["Component"].map(stock_dict).fillna(0)
            step["Shortage"] = (step["Required"] - step["Stock"]).clip(lower=0)
            
            all_results.append(step)
            
            # Next level input
            current = step[["Component", "Month", "Alt", "Shortage"]].rename(columns={"Shortage": "Demand"})

        # 7. Final Pivot
        if all_results:
            final = pd.concat(all_results)
            report = final.groupby(["Component", "Month"])["Required"].sum().unstack().fillna(0).reset_index()
            st.success("MRP Run Successful!")
            st.dataframe(report)
            
            # Download
            output = BytesIO()
            report.to_excel(output, index=False)
            st.download_button("Download Excel", output.getvalue(), "MRP_Report.xlsx")
        else:
            st.warning("No requirements found.")
import streamlit as st
import pandas as pd
from io import BytesIO

# --- 1. PAGE CONFIG ---
st.set_page_config(page_title="MRP Shortage Tool", page_icon="⚙️", layout="wide")

# --- 2. LOGIN SYSTEM ---
def check_password():
    def password_entered():
        if (st.session_state["username"] in st.secrets["passwords"] and 
            st.session_state["password"] == st.secrets["passwords"][st.session_state["username"]]):
            st.session_state["password_correct"] = True
            del st.session_state["password"]  
            del st.session_state["username"]
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        st.markdown("### 🔐 Production Planning Login")
        st.text_input("User ID", on_change=password_entered, key="username")
        st.text_input("Passcode", type="password", on_change=password_entered, key="password")
        return False
    elif not st.session_state["password_correct"]:
        st.markdown("### 🔐 Production Planning Login")
        st.text_input("User ID", on_change=password_entered, key="username")
        st.text_input("Passcode", type="password", on_change=password_entered, key="password")
        st.error("😕 Access Denied")
        return False
    return True

# --- 3. THE APP ---
if check_password():
    st.title("⚙️ MRP Shortage Analysis (Phantom Optimized)")
    
    with st.sidebar:
        st.header("📂 Data Upload")
        bom_file = st.file_uploader("1. BOM Master File", type=["xlsx", "xls", "xlsb"])
        req_file = st.file_uploader("2. Req & Stock File", type=["xlsx", "xls", "xlsb"])

    def read_excel_safe(uploaded_file, sheet_name=None):
        uploaded_file.seek(0)
        for engine in ["openpyxl", "pyxlsb", "xlrd"]:
            try: return pd.read_excel(uploaded_file, sheet_name=sheet_name, dtype=str, engine=engine)
            except: continue
        return pd.read_excel(uploaded_file, sheet_name=sheet_name, dtype=str)

    if bom_file and req_file:
        if st.sidebar.button("🚀 Run MRP Engine"):
            progress_bar = st.progress(0)
            
            # Load Data
            bom = read_excel_safe(bom_file, sheet_name=0)
            req = read_excel_safe(req_file, sheet_name="Requirement")
            stock = read_excel_safe(req_file, sheet_name="Stock")
            
            # Clean Columns
            for df in [bom, req, stock]:
                df.columns = df.columns.str.strip()
            bom.rename(columns={"Alt.": "Alt", "Special procurement": "SP"}, inplace=True)
            req.rename(columns={"Alt.": "Alt"}, inplace=True)

            # Normalize IDs
            def normalize(x):
                if pd.isna(x): return ""
                x = str(x).strip()
                if x.endswith(".0"): x = x[:-2]
                return x.zfill(10)

            bom["Component"] = bom["Component"].apply(normalize)
            bom["BOM Header"] = bom["BOM Header"].apply(normalize)
            stock["Component"] = stock["Component"].apply(normalize)
            req["BOM Header"] = req["BOM Header"].apply(normalize)
            
            # Numeric Conversion
            bom["Level"] = pd.to_numeric(bom["Level"], errors="coerce")
            bom["Quantity"] = pd.to_numeric(bom.get("Required Qty", bom.get("Quantity", 0)), errors="coerce").fillna(0)
            stock["Stock"] = pd.to_numeric(stock["Quantity"].astype(str).str.replace(",", ""), errors="coerce").fillna(0)

            # Build Parent Logic & identify Phantom Parents
            parents = []
            parent_sp = [] # To track if the direct parent is a phantom
            stack = {}
            sp_stack = {}
            
            for i in range(len(bom)):
                lvl = bom.loc[i, "Level"]
                curr_comp = bom.loc[i, "Component"]
                curr_sp = str(bom.loc[i, "SP"]).strip()
                
                # Identify Parent
                p_comp = bom.loc[i, "BOM Header"] if lvl == 1 else stack.get(lvl - 1)
                p_sp = "0" if lvl == 1 else sp_stack.get(lvl - 1, "0")
                
                parents.append(p_comp)
                parent_sp.append(p_sp)
                
                stack[lvl] = curr_comp
                sp_stack[lvl] = curr_sp

            bom["Parent Component"] = parents
            bom["Parent_Is_Phantom"] = [True if x == "50" else False for x in parent_sp]

            # Melt Requirements
            req_long = req.melt(id_vars=["BOM Header", "Alt"], var_name="Month", value_name="Demand")
            req_long["Demand"] = pd.to_numeric(req_long["Demand"], errors="coerce").fillna(0)
            req_long = req_long[req_long["Demand"] > 0].rename(columns={"BOM Header": "Component"})

            current = req_long.copy()
            results = []
            max_level = int(bom["Level"].max())

            # --- MRP EXPLOSION LOOP ---
            for lvl in range(1, max_level + 1):
                level_bom = bom[bom["Level"] == lvl]
                merged = current.merge(level_bom, left_on=["Component", "Alt"], right_on=["Parent Component", "Alt"], how="inner")
                
                if merged.empty: continue

                # REVISED LOGIC: 
                # If Parent is Phantom, Usage = 1 (pass through Parent Demand).
                # Else, Usage = Parent Demand * Child Qty.
                merged["Gross_Req"] = merged.apply(
                    lambda x: x["Demand"] * 1 if x["Parent_Is_Phantom"] else x["Demand"] * x["Quantity"], 
                    axis=1
                )

                grouped = merged.groupby(["Component_y", "Month", "Alt"], as_index=False)["Gross_Req"].sum()
                grouped = grouped.rename(columns={"Component_y": "Component", "Gross_Req": "Required"})

                # Stock Consumption
                grouped = grouped.merge(stock[["Component", "Stock"]], on="Component", how="left").fillna(0)
                grouped["Shortage"] = (grouped["Required"] - grouped["Stock"]).clip(lower=0)

                results.append(grouped)
                current = grouped[["Component", "Month", "Alt", "Shortage"]].rename(columns={"Shortage": "Demand"})
            
            # --- FINAL REPORT GENERATION ---
            if results:
                all_req = pd.concat(results, ignore_index=True)
                pivot = all_req.groupby(["Component", "Month"])["Required"].sum().reset_index()
                pivot = pivot.pivot(index="Component", columns="Month", values="Required").fillna(0).reset_index()
                pivot = pivot.merge(stock[["Component", "Stock"]], on="Component", how="left").fillna(0)
                
                # Add Metadata
                meta = bom[["Component", "Component descriptio", "Procurement type", "SP"]].drop_duplicates("Component")
                pivot = pivot.merge(meta, on="Component", how="left")

                # Monthly Running Balance
                month_cols = [c for c in pivot.columns if "-" in str(c)]
                for i, m in enumerate(month_cols):
                    if i == 0: pivot[m] = pivot["Stock"] - pivot[m]
                    else: pivot[m] = pivot[month_cols[i-1]] - pivot[m]

                st.success("Analysis Complete")
                st.dataframe(pivot.style.applymap(lambda x: 'color: red' if isinstance(x, (int, float)) and x < 0 else None))
                
                output = BytesIO()
                pivot.to_excel(output, index=False)
                st.download_button("📥 Download Report", output.getvalue(), "MRP_Phantom_Report.xlsx")
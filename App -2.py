import streamlit as st
import pandas as pd
from io import BytesIO

# --- 1. PAGE CONFIG ---
st.set_page_config(page_title="MRP Shortage Tool", page_icon="⚙️", layout="wide")

# --- 2. LOGIN SYSTEM ---
def check_password():
    if "password_correct" not in st.session_state:
        st.session_state["password_correct"] = False
    if st.session_state["password_correct"]:
        return True

    st.markdown("### 🔐 Production Planning Login")
    user = st.text_input("User ID", key="username")
    pas = st.text_input("Passcode", type="password", key="password")
    
    if st.button("Login"):
        if user in st.secrets["passwords"] and pas == st.secrets["passwords"][user]:
            st.session_state["password_correct"] = True
            st.rerun()
        else:
            st.error("😕 Access Denied")
    return False

# --- 3. THE APP ---
if check_password():
    st.title("⚙️ MRP Shortage Analysis Dashboard")
    
    with st.sidebar:
        st.success("✅ Access Granted")
        if st.button("Logout"):
            st.session_state["password_correct"] = False
            st.rerun()
        st.markdown("---")
        st.header("📂 Data Upload")
        bom_file = st.file_uploader("1. BOM Master File", type=["xlsx", "xls", "xlsb"])
        req_file = st.file_uploader("2. Req & Stock File", type=["xlsx", "xls", "xlsb"])

    def read_excel_safe(uploaded_file, sheet_name=None):
        uploaded_file.seek(0)
        for engine in ["openpyxl", "pyxlsb", "xlrd"]:
            try:
                return pd.read_excel(uploaded_file, sheet_name=sheet_name, dtype=str, engine=engine)
            except:
                continue
        return pd.read_excel(uploaded_file, sheet_name=sheet_name, dtype=str)

    if bom_file and req_file:
        if st.sidebar.button("🚀 Run MRP Engine"):
            # 1. LOAD DATA
            bom = read_excel_safe(bom_file, sheet_name=0)
            req = read_excel_safe(req_file, sheet_name="Requirement")
            stock = read_excel_safe(req_file, sheet_name="Stock")

            # 2. CLEAN & NORMALIZE
            bom.columns = bom.columns.str.strip()
            req.columns = req.columns.str.strip()
            stock.columns = stock.columns.str.strip()
            
            # Map Alt and SP columns
            bom.rename(columns={"Alt.": "Alt", "SP type": "SP", "Special procurement": "SP"}, inplace=True)
            req.rename(columns={"Alt.": "Alt"}, inplace=True)

            def normalize(x):
                if pd.isna(x): return ""
                x = str(x).strip()
                if x.endswith(".0"): x = x[:-2]
                return x.zfill(10)

            bom["Component"] = bom["Component"].apply(normalize)
            bom["BOM Header"] = bom["BOM Header"].apply(normalize)
            stock["Component"] = stock["Component"].apply(normalize)
            req["BOM Header"] = req["BOM Header"].apply(normalize)

            # --- THE PHANTOM FIX ---
            # Use 'Required Qty' as per your old logic
            bom["Required Qty"] = pd.to_numeric(bom["Required Qty"], errors="coerce").fillna(0)
            
            # RULE: If a component is a Phantom (SP 50), its 'Required Qty' is forced to 1
            # This ensures it doesn't over-multiply its children later.
            bom.loc[bom["SP"].astype(str) == "50", "Required Qty"] = 1.0

            bom["Level"] = pd.to_numeric(bom["Level"], errors="coerce")
            stock["Stock"] = pd.to_numeric(stock["Quantity"], errors="coerce").fillna(0)
            stock_dict = stock.set_index("Component")["Stock"].to_dict()

            # 3. BUILD PARENT RELATIONSHIP (Old Logic)
            parents = []
            stack = {}
            for i in range(len(bom)):
                lvl = bom.loc[i, "Level"]
                comp = bom.loc[i, "Component"]
                p_id = bom.loc[i, "BOM Header"] if lvl == 1 else stack.get(lvl - 1)
                parents.append(p_id)
                stack[lvl] = comp
            
            bom["Parent Component"] = parents

            # 4. EXPLOSION
            req_long = req.melt(id_vars=["BOM Header", "Alt"], var_name="Month", value_name="Demand")
            req_long["Demand"] = pd.to_numeric(req_long["Demand"], errors="coerce").fillna(0)
            req_long = req_long[req_long["Demand"] > 0].rename(columns={"BOM Header": "Component"})

            current = req_long.copy()
            results = []
            max_level = int(bom["Level"].max())

            for lvl in range(1, max_level + 1):
                level_bom = bom[bom["Level"] == lvl]
                merged = current.merge(level_bom, left_on=["Component", "Alt"], right_on=["Parent Component", "Alt"], how="inner")
                
                if merged.empty: continue

                # Back to your original calculation logic:
                # Since Phantoms are now 1.0, this handles everything correctly.
                merged["Gross_Req"] = merged["Demand"] * merged["Required Qty"]

                grouped = merged.groupby(["Component_y", "Month", "Alt"], as_index=False)["Gross_Req"].sum()
                grouped = grouped.rename(columns={"Component_y": "Component", "Gross_Req": "Required"})

                # Subtract Stock
                grouped["Stock"] = grouped["Component"].map(stock_dict).fillna(0)
                grouped["Shortage"] = (grouped["Required"] - grouped["Stock"]).clip(lower=0)

                results.append(grouped)
                current = grouped[["Component", "Month", "Alt", "Shortage"]].rename(columns={"Shortage": "Demand"})

            # 5. FINAL REPORT
            if results:
                all_data = pd.concat(results, ignore_index=True)
                # Group by Component and Month to sum up requirements across levels
                pivot = all_data.groupby(["Component", "Month"])["Required"].sum().unstack().fillna(0).reset_index()
                
                st.subheader("📋 MRP Shortage Report")
                st.dataframe(pivot, use_container_width=True)
                
                output = BytesIO()
                pivot.to_excel(output, index=False)
                st.download_button("📥 Download Excel Report", output.getvalue(), "MRP_Final_Report.xlsx")
            else:
                st.error("No requirements generated. Please check your data.")
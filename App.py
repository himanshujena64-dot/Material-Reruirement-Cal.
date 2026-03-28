import streamlit as st
import pandas as pd
from io import BytesIO

# --- 1. PAGE CONFIG ---
st.set_page_config(page_title="App-2: MRP Calculation", layout="wide")

# --- 2. AUTHENTICATION ---
def check_password():
    if "passwords" not in st.secrets:
        st.error("🔑 Secrets not configured. Please add them in Streamlit Cloud Settings.")
        return False

    def password_entered():
        if st.session_state["username"] in st.secrets["passwords"] and \
           st.session_state["password"] == st.secrets["passwords"][st.session_state["username"]]:
            st.session_state["password_correct"] = True
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        st.title("🔐 Login to App-2")
        st.text_input("Username", key="username")
        st.text_input("Password", type="password", key="password")
        st.button("Login", on_click=password_entered)
        return False
    return st.session_state.get("password_correct", False)

if check_password():
    st.title("📊 App-2: MRP Requirement Calculation")

    with st.sidebar:
        st.header("📂 Data Upload")
        bom_file = st.file_uploader("1. BOM Master File", type=["xlsx"])
        req_file = st.file_uploader("2. Req & Stock File", type=["xlsx"])

    def read_excel_safe(uploaded_file, sheet_name=None):
        if uploaded_file is None: return None
        uploaded_file.seek(0)
        try:
            data = pd.read_excel(uploaded_file, sheet_name=sheet_name, engine="openpyxl")
            return list(data.values())[0] if isinstance(data, dict) else data
        except Exception as e:
            st.error(f"Sheet Error: Ensure '{sheet_name}' exists in the uploaded file.")
            return None

    def normalize(x):
        if pd.isna(x): return ""
        x = str(x).strip()
        return x[:-2].upper() if x.endswith(".0") else x.upper()

    if bom_file and req_file:
        if st.sidebar.button("Calculate Requirement"):
            try:
                # LOAD
                bom = read_excel_safe(bom_file)
                req = read_excel_safe(req_file, "Requirement")
                stock = read_excel_safe(req_file, "Stock")

                if bom is None or req is None or stock is None: st.stop()

                # CLEAN COLUMNS
                bom.columns = [str(c).strip() for c in bom.columns]
                req.columns = [str(c).strip() for c in req.columns]
                stock.columns = [str(c).strip() for c in stock.columns]

                # NORMALIZE
                bom["Component"] = bom["Component"].apply(normalize)
                bom["BOM Header"] = bom["BOM Header"].apply(normalize)
                req["BOM Header"] = req["BOM Header"].apply(normalize)
                stock["Component"] = stock["Component"].apply(normalize)

                # DYNAMIC ID_VARS CHECK (Fixes the 'Alt' crash)
                possible_ids = ["BOM Header", "Alt"]
                actual_ids = [c for c in possible_ids if c in req.columns]
                month_cols = [c for c in req.columns if c not in actual_ids]

                # MELT DATA
                current_demand = req.melt(id_vars=actual_ids, value_vars=month_cols, 
                                         var_name="Month", value_name="Demand")
                current_demand["Demand"] = pd.to_numeric(current_demand["Demand"], errors="coerce").fillna(0.0)
                current_demand = current_demand.rename(columns={"BOM Header": "Parent"})

                # MRP CALCULATION LOGIC
                results = []
                # Ensure the merge logic works even if 'Alt' is missing from one file
                merge_cols = ["Alt"] if "Alt" in bom.columns and "Alt" in current_demand.columns else []

                for lvl in range(1, int(bom["Level"].max()) + 1):
                    bom_lvl = bom[bom["Level"] == lvl]
                    merged = current_demand.merge(bom_lvl, left_on=["Parent"] + merge_cols, 
                                                 right_on=["BOM Header"] + merge_cols, how="inner")
                    if merged.empty: break
                    
                    merged["Gross"] = merged["Demand"] * pd.to_numeric(merged["Quantity"], errors='coerce').fillna(0)
                    results.append(merged)
                    
                    # Update demand for next level
                    current_demand = merged[["Component", "Month", "Gross"]].rename(columns={"Component": "Parent", "Gross": "Demand"})
                    if "Alt" in merged.columns:
                        current_demand["Alt"] = merged["Alt"]

                if results:
                    final = pd.concat(results)
                    pivot = final.groupby(["Component", "Month"])["Gross"].sum().unstack().fillna(0)
                    st.success("✅ Calculation Complete")
                    st.dataframe(pivot)
                    
                    out = BytesIO()
                    pivot.to_excel(out)
                    st.download_button("📥 Download Results", out.getvalue(), "MRP_Result.xlsx")
                else:
                    st.warning("No matches found between BOM and Requirements.")

            except Exception as e:
                st.error(f"Calculation Error: {e}")

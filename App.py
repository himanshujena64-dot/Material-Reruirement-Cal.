import streamlit as st
import pandas as pd
from io import BytesIO

# --- 1. PAGE CONFIG ---
st.set_page_config(page_title="App-2: MRP Calculation", layout="wide")

# --- 2. AUTHENTICATION (Streamlit Cloud Secrets) ---
def check_password():
    """Returns True if the user had the correct password."""
    if "passwords" not in st.secrets:
        st.error("Secrets not configured. Please add passwords to .streamlit/secrets.toml")
        return False

    def password_entered():
        if st.session_state["username"] in st.secrets["passwords"] and \
           st.session_state["password"] == st.secrets["passwords"][st.session_state["username"]]:
            st.session_state["password_correct"] = True
            del st.session_state["password"]  # don't store password
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        st.text_input("Username", key="username")
        st.text_input("Password", type="password", key="password")
        st.button("Login", on_click=password_entered)
        return False
    elif not st.session_state["password_correct"]:
        st.text_input("Username", key="username")
        st.text_input("Password", type="password", key="password")
        st.button("Login", on_click=password_entered)
        st.error("😕 User not known or password incorrect")
        return False
    else:
        return True

if check_password():
    st.title("📊 App-2: MRP Requirement Calculation")

    # --- 3. SIDEBAR ---
    with st.sidebar:
        st.header("📂 Data Upload")
        bom_file = st.file_uploader("1. BOM Master File", type=["xlsx"])
        req_file = st.file_uploader("2. Req & Stock File", type=["xlsx"])

    # --- 4. UTILITIES ---
    def read_excel_safe(uploaded_file, sheet_name=None):
        if uploaded_file is None:
            return None
        uploaded_file.seek(0)
        try:
            # Explicitly handling the dict-to-dataframe conversion
            data = pd.read_excel(uploaded_file, sheet_name=sheet_name, engine="openpyxl")
            if isinstance(data, dict):
                # If sheet_name wasn't specified and multiple sheets exist, take the first one
                return list(data.values())[0]
            return data
        except Exception as e:
            st.error(f"File Load Error: {e}")
            return None

    def normalize(x):
        if pd.isna(x): return ""
        x = str(x).strip()
        if x.endswith(".0"): x = x[:-2]
        return x.upper()

    # --- 5. ENGINE ---
    if bom_file and req_file:
        if st.sidebar.button("Calculate Requirement"):
            try:
                # A. LOAD DATA
                bom_raw = read_excel_safe(bom_file)
                req_raw = read_excel_safe(req_file, "Requirement")
                stock_raw = read_excel_safe(req_file, "Stock")

                if bom_raw is None or req_raw is None or stock_raw is None:
                    st.error("❌ Critical Error: Ensure sheets 'Requirement' and 'Stock' exist in File 2.")
                    st.stop()

                # B. CLEAN & NORMALIZE
                # Ensure we are working with DataFrames (fixing the 'dict' error)
                bom = bom_raw.copy().rename(columns=lambda x: str(x).strip())
                req = req_raw.copy().rename(columns=lambda x: str(x).strip())
                stock = stock_raw.copy().rename(columns=lambda x: str(x).strip())

                for df in [bom, stock]: 
                    df["Component"] = df["Component"].apply(normalize)
                bom["BOM Header"] = bom["BOM Header"].apply(normalize)
                req["BOM Header"] = req["BOM Header"].apply(normalize)

                # C. NUMERIC PREP (Preserving Decimals for 10,859.598 result)
                bom["Level"] = pd.to_numeric(bom["Level"], errors="coerce").fillna(0)
                qty_col = "Required Qty" if "Required Qty" in bom.columns else "Quantity"
                bom["Qty"] = pd.to_numeric(bom[qty_col], errors="coerce").fillna(0.0)
                
                stock = stock.rename(columns={"Quantity": "Stock_Qty"})
                stock["Stock_Qty"] = pd.to_numeric(stock["Stock_Qty"], errors="coerce").fillna(0.0)

                # D. INITIAL DEMAND SETUP
                id_cols = ["BOM Header", "Alt"]
                month_cols = [c for c in req.columns if c not in id_cols]
                
                current_demand = req.melt(id_vars=id_cols, value_vars=month_cols, 
                                         var_name="Month", value_name="Demand")
                current_demand["Demand"] = pd.to_numeric(current_demand["Demand"], errors="coerce").fillna(0.0)
                current_demand = current_demand.rename(columns={"BOM Header": "Parent"})

                # E. MULTI-LEVEL EXPLOSION
                results = []
                max_depth = int(bom["Level"].max())

                for lvl in range(1, max_depth + 1):
                    bom_lvl = bom[bom["Level"] == lvl][["BOM Header", "Component", "Alt", "Qty", "SP"]]
                    merged = current_demand.merge(bom_lvl, left_on=["Parent", "Alt"], right_on=["BOM Header", "Alt"], how="inner")
                    if merged.empty: break
                    
                    merged["Gross"] = merged["Demand"] * merged["Qty"]
                    merged = merged.merge(stock[["Component", "Stock_Qty"]], on="Component", how="left")
                    merged["Stock_Qty"] = merged["Stock_Qty"].fillna(0.0)
                    
                    # Logic: Subtract stock at every level to get Net Shortage
                    merged["Shortage"] = merged.apply(
                        lambda x: x["Gross"] if str(x.get("SP")) == "50" else max(0.0, x["Gross"] - x["Stock_Qty"]), 
                        axis=1
                    )
                    
                    results.append(merged[["Component", "Month", "Gross", "Shortage"]])
                    current_demand = merged[["Component", "Month", "Alt", "Shortage"]].rename(
                        columns={"Component": "Parent", "Shortage": "Demand"}
                    )

                # F. FINAL PIVOT
                if results:
                    final_data = pd.concat(results, ignore_index=True)
                    pivot_df = final_data.groupby(["Component", "Month"])["Gross"].sum().unstack().fillna(0.0)
                    
                    # Align with original month order
                    final_month_cols = [m for m in month_cols if m in pivot_df.columns]
                    pivot_df = pivot_df[final_month_cols]
                    
                    # Merge descriptive info
                    info = bom[["Component", "Component descriptio", "Procurement type", "Special procurement"]].drop_duplicates("Component")
                    final_report = pivot_df.merge(info, on="Component", how="left")
                    final_report = final_report.merge(stock[["Component", "Stock_Qty"]], on="Component", how="left")
                    
                    st.success("✅ Calculation Complete")
                    st.dataframe(final_report, use_container_width=True)

                    output = BytesIO()
                    final_report.to_excel(output, index=False)
                    st.download_button("📥 Download Report", output.getvalue(), "MRP_Results.xlsx")
                else:
                    st.warning("⚠️ No matches found.")

            except Exception as e:
                st.error(f"Execution Error: {e}")
    else:
        st.info("Upload BOM and Requirement files to start.")

# Concluded updated logic for App-2

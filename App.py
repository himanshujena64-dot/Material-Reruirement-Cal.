import streamlit as st
import pandas as pd
from io import BytesIO

# --- 1. PAGE CONFIG ---
st.set_page_config(page_title="App-2: MRP Calculation", layout="wide")

# --- 2. AUTHENTICATION ---
def check_password():
    if "passwords" not in st.secrets:
        st.error("🔑 **Secrets not configured.** Please add your passwords to the Streamlit Cloud Settings -> Secrets.")
        st.info("Format: [passwords] \\n user = 'password'")
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
    elif not st.session_state["password_correct"]:
        st.text_input("Username", key="username")
        st.text_input("Password", type="password", key="password")
        st.button("Login", on_click=password_entered)
        st.error("❌ Invalid Username or Password")
        return False
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
        if uploaded_file is None: return None
        uploaded_file.seek(0)
        try:
            # Handle cases where Excel returns a dict or single DataFrame
            data = pd.read_excel(uploaded_file, sheet_name=sheet_name, engine="openpyxl")
            if isinstance(data, dict):
                return list(data.values())[0]
            return data
        except Exception as e:
            st.error(f"Error reading file: {e}")
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
                # LOAD
                bom_df = read_excel_safe(bom_file)
                req_df = read_excel_safe(req_file, "Requirement")
                stock_df = read_excel_safe(req_file, "Stock")

                if bom_df is None or req_df is None or stock_df is None:
                    st.stop()

                # CLEAN & NORMALIZE
                bom = bom_df.copy().rename(columns=lambda x: str(x).strip())
                req = req_df.copy().rename(columns=lambda x: str(x).strip())
                stock = stock_df.copy().rename(columns=lambda x: str(x).strip())

                for df in [bom, stock]: df["Component"] = df["Component"].apply(normalize)
                bom["BOM Header"] = bom["BOM Header"].apply(normalize)
                req["BOM Header"] = req["BOM Header"].apply(normalize)

                # NUMERIC (Ensure 10,859.598 accuracy)
                bom["Level"] = pd.to_numeric(bom["Level"], errors="coerce").fillna(0)
                qty_col = "Required Qty" if "Required Qty" in bom.columns else "Quantity"
                bom["Qty"] = pd.to_numeric(bom[qty_col], errors="coerce").fillna(0.0)
                
                stock = stock.rename(columns={"Quantity": "Stock_Qty"})
                stock["Stock_Qty"] = pd.to_numeric(stock["Stock_Qty"], errors="coerce").fillna(0.0)

                # DEMAND INITIALIZATION
                id_cols = ["BOM Header", "Alt"]
                month_cols = [c for c in req.columns if c not in id_cols]
                
                current_demand = req.melt(id_vars=id_cols, value_vars=month_cols, 
                                         var_name="Month", value_name="Demand")
                current_demand["Demand"] = pd.to_numeric(current_demand["Demand"], errors="coerce").fillna(0.0)
                current_demand = current_demand.rename(columns={"BOM Header": "Parent"})

                # MULTI-LEVEL LOGIC
                results = []
                max_depth = int(bom["Level"].max())

                for lvl in range(1, max_depth + 1):
                    bom_lvl = bom[bom["Level"] == lvl][["BOM Header", "Component", "Alt", "Qty", "SP"]]
                    merged = current_demand.merge(bom_lvl, left_on=["Parent", "Alt"], right_on=["BOM Header", "Alt"], how="inner")
                    if merged.empty: break
                    
                    merged["Gross"] = merged["Demand"] * merged["Qty"]
                    merged = merged.merge(stock[["Component", "Stock_Qty"]], on="Component", how="left")
                    merged["Stock_Qty"] = merged["Stock_Qty"].fillna(0.0)
                    
                    # Shortage logic for Level explosion
                    merged["Shortage"] = merged.apply(
                        lambda x: x["Gross"] if str(x.get("SP")) == "50" else max(0.0, x["Gross"] - x["Stock_Qty"]), axis=1
                    )
                    
                    results.append(merged[["Component", "Month", "Gross", "Shortage"]])
                    current_demand = merged[["Component", "Month", "Alt", "Shortage"]].rename(
                        columns={"Component": "Parent", "Shortage": "Demand"}
                    )

                # FINAL OUTPUT
                if results:
                    final_data = pd.concat(results, ignore_index=True)
                    pivot = final_data.groupby(["Component", "Month"])["Gross"].sum().unstack().fillna(0.0)
                    
                    # Match original months
                    existing_months = [m for m in month_cols if m in pivot.columns]
                    pivot = pivot[existing_months]
                    
                    # Merge description and stock for the final table
                    info = bom[["Component", "Component descriptio", "Procurement type"]].drop_duplicates("Component")
                    final_report = pivot.merge(info, on="Component", how="left")
                    final_report = final_report.merge(stock[["Component", "Stock_Qty"]], on="Component", how="left")
                    
                    st.success("✅ MRP Run Successful")
                    st.dataframe(final_report, use_container_width=True)

                    # EXPORT
                    out = BytesIO()
                    final_report.to_excel(out, index=False)
                    st.download_button("📥 Download Excel Report", out.getvalue(), "MRP_App2_Results.xlsx")
                else:
                    st.warning("⚠️ No components exploded. Check if BOM Headers match Requirements.")

            except Exception as e:
                st.error(f"Calculation Error: {e}")
    else:
        st.info("Please upload BOM and Requirement files to start.")

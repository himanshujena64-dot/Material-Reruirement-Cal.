import streamlit as st
import pandas as pd

# --- CORE MRP LOGIC ---
def calculate_recursive_demand(parent_pn, current_demand, current_level, target_pn, bom_df, stock_dict):
    total_gross_for_target = 0
    
    # Filter children: Match Header, Level, and Alt 10
    # Using .astype(str) to ensure PN comparison works
    children = bom_df[(bom_df['BOM Header'].astype(str) == str(parent_pn)) & 
                      (bom_df['Level'] == current_level + 1) & 
                      (bom_df['Alt.'] == 10)]
    
    for _, row in children.iterrows():
        child_pn = str(row['Component'])
        # 'Required Qty' is the quantity needed per 1 unit of parent
        req_qty = float(row['Required Qty'])
        spec_proc = str(row.get('Special procurement', ''))
        
        child_gross_req = current_demand * req_qty
        
        if child_pn == target_pn:
            total_gross_for_target += child_gross_req
        else:
            child_stock = float(stock_dict.get(child_pn, 0))
            
            # PHANTOM LOGIC: If SP is 50, ignore stock and pass all demand
            if spec_proc == "50":
                pass_down_qty = child_gross_req
            else:
                pass_down_qty = max(0, child_gross_req - child_stock)
            
            if pass_down_qty > 0:
                total_gross_for_target += calculate_recursive_demand(
                    child_pn, pass_down_qty, current_level + 1, target_pn, bom_df, stock_dict
                )
                
    return total_gross_for_target

# --- STREAMLIT UI ---
st.set_page_config(page_title="App-2 MRP", layout="wide")
st.title("📦 App-2: MRP Requirement Calculator")

st.sidebar.header("Upload Files")
# Only 2 Uploaders
bom_file = st.sidebar.file_uploader("1. Upload BOM (bom as on 1503.XLSX)", type=['xlsx'])
data_file = st.sidebar.file_uploader("2. Upload Req & Stock File", type=['xlsx'])

target_part = st.sidebar.text_input("Target Component PN", value="0010300601DEL")

if bom_file and data_file:
    try:
        # 1. Load BOM
        df_bom = pd.read_excel(bom_file)
        
        # 2. Load Req & Stock Sheets
        xls = pd.ExcelFile(data_file)
        sheet_names = xls.sheet_names
        
        # Auto-detect sheet names
        req_sheet = [s for s in sheet_names if 'Req' in s][0]
        stock_sheet = [s for s in sheet_names if 'Stock' in s][0]
        
        df_demand = pd.read_excel(data_file, sheet_name=req_sheet)
        df_stock = pd.read_excel(data_file, sheet_name=stock_sheet)

        # 3. Process Stock Dictionary
        # Your file uses 'Quantity' for stock
        stock_col = 'Quantity' if 'Quantity' in df_stock.columns else 'Avl. Stock'
        stock_dict = pd.Series(df_stock[stock_col].values, 
                               index=df_stock['Component'].astype(str)).to_dict()

        if st.button("Calculate Requirement"):
            total_gross_req = 0
            
            # 4. Process Demand (Targeting Jan-26 column)
            demand_col = [c for c in df_demand.columns if 'Jan-26' in str(c)][0]
            
            for _, row in df_demand.iterrows():
                header = str(row['BOM Header'])
                qty = float(row[demand_col])
                
                if qty > 0:
                    total_gross_req += calculate_recursive_demand(
                        header, qty, 0, target_part, df_bom, stock_dict
                    )
            
            # 5. Final Calculation for Target Component
            available_stock = float(stock_dict.get(target_part, 0))
            shortage = max(0, total_gross_req - available_stock)
            
            st.divider()
            c1, c2, c3 = st.columns(3)
            c1.metric("Total Gross Req", f"{total_gross_req:,.3f}")
            c2.metric("Available Stock", f"{available_stock:,.3f}")
            c3.metric("Net Shortage", f"{shortage:,.3f}")

            if shortage > 0:
                st.error(f"Action: Need to order {shortage:,.3f} units of {target_part}.")
            else:
                st.success(f"Stock for {target_part} is sufficient.")

    except Exception as e:
        st.error(f"File Error: {e}")
else:
    st.info("Please upload both Excel files to proceed.")

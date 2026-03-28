import streamlit as st
import pandas as pd

# --- CORE MRP LOGIC ---
def calculate_recursive_demand(parent_pn, current_demand, current_level, target_pn, bom_df, stock_dict):
    total_gross_for_target = 0
    
    # Matching your BOM structure: BOM Header, Level, Alt. 10
    children = bom_df[(bom_df['BOM Header'].astype(str) == str(parent_pn)) & 
                      (bom_df['Level'] == current_level + 1) & 
                      (bom_df['Alt.'] == 10)]
    
    for _, row in children.iterrows():
        child_pn = str(row['Component'])
        req_qty = float(row['Required Qty'])
        spec_proc = str(row.get('Special procurement', ''))
        
        child_gross_req = current_demand * req_qty
        
        if child_pn == target_pn:
            total_gross_for_target += child_gross_req
        else:
            child_stock = float(stock_dict.get(child_pn, 0))
            
            # Phantom Logic: Special Procurement 50
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
st.set_page_config(page_title="MRP App-2", layout="wide")
st.title("📊 MRP Logic Corrector (App-2)")

st.sidebar.header("Upload Files")
bom_file = st.sidebar.file_uploader("1. Upload BOM (bom as on 1503.XLSX)", type=['xlsx'])
req_stock_file = st.sidebar.file_uploader("2. Upload Req & Stock File", type=['xlsx'])

target_part = st.sidebar.text_input("Target Component", value="0010300601DEL")

if bom_file and req_stock_file:
    try:
        df_bom = pd.read_excel(bom_file)
        all_sheets = pd.read_excel(req_stock_file, sheet_name=None)
        
        # Finding sheets by keyword
        req_key = [k for k in all_sheets.keys() if 'Req' in k][0]
        stock_key = [k for k in all_sheets.keys() if 'Stock' in k][0]
        
        df_demand = all_sheets[req_key]
        df_stock = all_sheets[stock_key]

        # Map stock using 'Component' and 'Avl. Stock' from your file
        stock_dict = pd.Series(df_stock['Avl. Stock'].values, 
                               index=df_stock['Component'].astype(str)).to_dict()

        if st.button("Run Calculation"):
            total_gross_req = 0
            
            # Using 'Jan-26 Demand' column from your Requirement sheet
            demand_col = [c for c in df_demand.columns if 'Jan-26' in c][0]
            
            for _, row in df_demand.iterrows():
                header = str(row['BOM Header'])
                qty = float(row[demand_col])
                total_gross_req += calculate_recursive_demand(header, qty, 0, target_part, df_bom, stock_dict)
            
            # Final shortage math
            target_stock = float(stock_dict.get(target_part, 0))
            shortage = max(0, total_gross_req - target_stock)
            
            st.divider()
            col1, col2, col3 = st.columns(3)
            col1.metric("Total Gross Req", f"{total_gross_req:,.2f}")
            col2.metric("Available Stock", f"{target_stock:,.2f}")
            col3.metric("Net Shortage", f"{shortage:,.2f}")
            
            if shortage > 0:
                st.error(f"Need to order: {shortage:,.2f}")
            else:
                st.success("Stock is sufficient.")

    except Exception as e:
        st.error(f"Column/Sheet mismatch: {e}")
else:
    st.info("Please upload both Excel files to start.")
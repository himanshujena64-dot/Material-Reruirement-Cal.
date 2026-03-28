import streamlit as st
import pandas as pd

# --- CORE MRP LOGIC ---
def calculate_recursive_demand(parent_pn, current_demand, current_level, target_pn, bom_df, stock_dict):
    total_gross_for_target = 0
    
    # Force everything to string and strip spaces for matching
    parent_pn_str = str(parent_pn).strip()
    
    # Filter children: Match Header, Level, and Alt 10
    children = bom_df[(bom_df['BOM Header'].astype(str).str.strip() == parent_pn_str) & 
                      (bom_df['Level'] == current_level + 1) & 
                      (bom_df['Alt.'] == 10)]
    
    for _, row in children.iterrows():
        child_pn = str(row['Component']).strip()
        req_qty = float(row['Required Qty'])
        spec_proc = str(row.get('Special procurement', '')).strip()
        
        child_gross_req = current_demand * req_qty
        
        if child_pn == target_pn:
            total_gross_for_target += child_gross_req
        else:
            child_stock = float(stock_dict.get(child_pn, 0))
            
            # PHANTOM LOGIC: Special Procurement 50 passes demand 100%
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
st.set_page_config(page_title="App-2 MRP Calculator", layout="wide")
st.title("📊 App-2: MRP Requirement Calculation")

st.sidebar.header("Upload Files")
bom_file = st.sidebar.file_uploader("1. Upload BOM File", type=['xlsx'])
data_file = st.sidebar.file_uploader("2. Upload Req & Stock File", type=['xlsx'])

target_part = st.sidebar.text_input("Target Component PN", value="0010300601DEL")

if bom_file and data_file:
    try:
        # Load BOM
        df_bom = pd.read_excel(bom_file)
        # Clean BOM columns
        df_bom.columns = [str(c).strip() for c in df_bom.columns]
        
        # Load sheets from the second file
        xls = pd.ExcelFile(data_file)
        s_names = xls.sheet_names
        req_s = [s for s in s_names if 'Req' in s][0]
        stk_s = [s for s in s_names if 'Stock' in s][0]
        
        df_demand = pd.read_excel(data_file, sheet_name=req_s)
        df_stock = pd.read_excel(data_file, sheet_name=stk_s)
        
        # CLEAN ALL COLUMNS (Crucial for the 'Jan-26' fix)
        df_demand.columns = [str(c).strip() for c in df_demand.columns]
        df_stock.columns = [str(c).strip() for c in df_stock.columns]

        # Map Stock (Supports 'Quantity' or 'Avl. Stock')
        stock_col = [c for c in df_stock.columns if 'Quantity' in c or 'Stock' in c][0]
        stock_dict = pd.Series(df_stock[stock_col].values, 
                               index=df_stock['Component'].astype(str).str.strip()).to_dict()

        if st.button("Calculate Requirement"):
            total_gross_req = 0
            
            # FIND DEMAND COLUMN: Look for 'Jan-26' inside any column name
            demand_col_list = [c for c in df_demand.columns if 'Jan-26' in str(c)]
            
            if not demand_col_list:
                st.error(f"Could not find 'Jan-26' column. Available columns: {list(df_demand.columns)}")
            else:
                demand_col = demand_col_list[0]
                
                for _, row in df_demand.iterrows():
                    header = str(row['BOM Header']).strip()
                    val = row[demand_col]
                    # Handle potential non-numeric data
                    qty = float(val) if pd.notnull(val) and str(val).replace('.','',1).isdigit() else 0
                    
                    if qty > 0:
                        total_gross_req += calculate_recursive_demand(
                            header, qty, 0, target_part, df_bom, stock_dict
                        )
                
                # Final Calculations
                target_stock = float(stock_dict.get(target_part, 0))
                shortage = max(0, total_gross_req - target_stock)
                
                st.divider()
                c1, c2, c3 = st.columns(3)
                c1.metric("Total Gross Req", f"{total_gross_req:,.3f}")
                c2.metric("Available Stock", f"{target_stock:,.3f}")
                c3.metric("Net Shortage", f"{shortage:,.3f}")

                if shortage > 0:
                    st.error(f"Action: Order {shortage:,.3f} units of {target_part}.")
                else:
                    st.success(f"Stock for {target_part} is sufficient.")

    except Exception as e:
        st.error(f"Error: {e}")
else:
    st.info("Please upload both Excel files to proceed.")

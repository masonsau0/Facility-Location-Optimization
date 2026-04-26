"""Interactive CFLP dashboard.

Run with::

    streamlit run facility_location_app.py

Lets the user edit facility / customer tables, choose managerial constraints
(max open, force-open / force-close), re-solve in real time, and inspect the
network on a folium map plus a cost breakdown.
"""

from __future__ import annotations

import io
from pathlib import Path

import folium
import pandas as pd
import streamlit as st
from geopy.distance import geodesic
from streamlit_folium import st_folium

from facility_location import Customer, Facility, Network, solve

st.set_page_config(page_title="Facility Location Optimizer", layout="wide", page_icon="📍")

DEFAULT_DATA = Path("facility_data.xlsx")
TRANSPORT_RATE = 0.05    # $/unit/km
ROAD_FACTOR = 1.3        # great-circle → road distance


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@st.cache_data
def load_default():
    fac = pd.read_excel(DEFAULT_DATA, sheet_name="Facilities")
    cust = pd.read_excel(DEFAULT_DATA, sheet_name="Customers")
    return fac, cust


def build_transport_matrix(fac_df: pd.DataFrame, cust_df: pd.DataFrame,
                           rate: float, road_factor: float) -> pd.DataFrame:
    rows = []
    for _, f in fac_df.iterrows():
        row = {"Facility": f["Facility"]}
        for _, c in cust_df.iterrows():
            km = geodesic((f["Latitude"], f["Longitude"]), (c["Latitude"], c["Longitude"])).km
            row[c["Customer"]] = round(km * road_factor * rate, 2)
        rows.append(row)
    return pd.DataFrame(rows).set_index("Facility")


def make_network(fac_df: pd.DataFrame, cust_df: pd.DataFrame, tc_df: pd.DataFrame) -> Network:
    facilities = [
        Facility(name=row["Facility"], fixed_cost=row["Fixed Cost ($/yr)"],
                 capacity=row["Capacity (units/yr)"],
                 lat=row["Latitude"], lon=row["Longitude"])
        for _, row in fac_df.iterrows()
    ]
    customers = [
        Customer(name=row["Customer"], demand=row["Demand (units/yr)"],
                 lat=row["Latitude"], lon=row["Longitude"])
        for _, row in cust_df.iterrows()
    ]
    return Network(facilities=facilities, customers=customers, transport_cost=tc_df)


def render_map(fac_df: pd.DataFrame, cust_df: pd.DataFrame, sol):
    open_set = set(sol.open_facilities) if sol else set()
    center_lat = pd.concat([fac_df["Latitude"], cust_df["Latitude"]]).mean()
    center_lon = pd.concat([fac_df["Longitude"], cust_df["Longitude"]]).mean()
    m = folium.Map(location=[center_lat, center_lon], zoom_start=6, tiles="cartodbpositron")

    if sol and not sol.assignments.empty:
        fac_lookup = fac_df.set_index("Facility").to_dict("index")
        cust_lookup = cust_df.set_index("Customer").to_dict("index")
        for _, row in sol.assignments.iterrows():
            f = fac_lookup[row["facility"]]
            c = cust_lookup[row["customer"]]
            folium.PolyLine(
                [(f["Latitude"], f["Longitude"]), (c["Latitude"], c["Longitude"])],
                color="#dd8452", weight=2, opacity=0.7,
            ).add_to(m)

    for _, c in cust_df.iterrows():
        folium.CircleMarker(
            location=[c["Latitude"], c["Longitude"]],
            radius=5 + (c["Demand (units/yr)"] / 25),
            color="#4c72b0", fill=True, fill_color="#4c72b0", fill_opacity=0.8,
            popup=f"{c['Customer']} — demand {c['Demand (units/yr)']:.0f}",
        ).add_to(m)

    for _, f in fac_df.iterrows():
        is_open = f["Facility"] in open_set
        folium.Marker(
            location=[f["Latitude"], f["Longitude"]],
            popup=(f"{f['Facility']} ({'OPEN' if is_open else 'closed'})<br>"
                   f"capacity {f['Capacity (units/yr)']:.0f}<br>"
                   f"fixed cost ${f['Fixed Cost ($/yr)']:,.0f}/yr"),
            icon=folium.Icon(color="orange" if is_open else "gray", icon="industry", prefix="fa"),
        ).add_to(m)

    return m


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------


st.title("Capacitated Facility Location Optimizer")
st.caption("Decide which distribution centres to open, and which customer each one should serve, to minimise total annual cost.")

with st.expander("How to use this app", expanded=False):
    st.markdown("""
**What this app does in plain English.**
Imagine you run a company with 7 possible warehouse locations. Each
warehouse has a fixed cost to open (rent, salaries) and a maximum
capacity it can handle. You also have 10 customers, each with a
different demand and a different shipping cost from each warehouse.
The question: which warehouses should you open, and which warehouse
should serve each customer, to minimise your total cost? This app
solves that puzzle using **mixed-integer linear programming** — a
mathematical optimisation technique used everywhere in supply-chain
planning.

**Quick start (60 seconds).**
1. Look at the **Facilities** and **Customers** tables — these are
   editable. Click any cell to change the number.
2. Adjust the **Maximum facilities to open** slider in the sidebar
   (e.g. cap it at 3).
3. Click **Solve** — the optimiser figures out the cheapest plan.
4. See the **map** showing which warehouses opened (green pins) and
   which customer each one serves (lines).

**The key controls.**
- **Facilities table** — each row is a candidate warehouse with its
  fixed annual cost and capacity. You can add, delete, or edit rows.
- **Customers table** — each row is a customer with their demand and
  the per-unit shipping cost from each warehouse.
- **Maximum facilities to open** — you can force the model to open at
  most N warehouses. Lower = fewer warehouses open but possibly higher
  shipping cost. Try 1, 2, 3 and watch the trade-off.

**What the results show.**
- **Total cost** — sum of fixed open costs + variable shipping costs.
- **Map** — green pins = open warehouses, grey = closed. Lines connect
  each customer to the warehouse that serves them.
- **Assignment table** — exactly which customer goes to which
  warehouse, and how much it costs.

**Try this.** Set Maximum facilities to 1 first (cheap to open, expensive
to ship). Then 3. Then 5. Watch how the cost curve flattens — usually
opening a 4th or 5th warehouse doesn't save much over having 3.
""")

if "facilities" not in st.session_state:
    fac, cust = load_default()
    st.session_state["facilities"] = fac
    st.session_state["customers"] = cust

with st.sidebar:
    st.header("Network inputs")

    st.subheader("Facilities")
    st.caption("Edit any cell. Add or delete rows with the toolbar at the top of the table.")
    fac_df = st.data_editor(
        st.session_state["facilities"], num_rows="dynamic", key="fac_editor",
        column_config={
            "Facility": st.column_config.TextColumn(required=True),
            "Latitude": st.column_config.NumberColumn(format="%.4f"),
            "Longitude": st.column_config.NumberColumn(format="%.4f"),
            "Fixed Cost ($/yr)": st.column_config.NumberColumn(format="$%.0f", min_value=0),
            "Capacity (units/yr)": st.column_config.NumberColumn(min_value=0),
        },
    )

    st.subheader("Customers")
    cust_df = st.data_editor(
        st.session_state["customers"], num_rows="dynamic", key="cust_editor",
        column_config={
            "Customer": st.column_config.TextColumn(required=True),
            "Latitude": st.column_config.NumberColumn(format="%.4f"),
            "Longitude": st.column_config.NumberColumn(format="%.4f"),
            "Demand (units/yr)": st.column_config.NumberColumn(min_value=0),
        },
    )

    st.subheader("Solver options")
    max_open = st.slider("Max facilities to open", 1, max(1, len(fac_df)),
                          value=len(fac_df), help="Tightens the open-count cap.")
    forced_open = st.multiselect("Force open", fac_df["Facility"].tolist())
    forced_closed = st.multiselect("Force closed",
                                    [n for n in fac_df["Facility"].tolist() if n not in forced_open])

    st.subheader("Transport rate")
    rate = st.number_input("$ / unit / km", value=TRANSPORT_RATE, min_value=0.0, step=0.005, format="%.3f")
    road_factor = st.number_input("Road / great-circle factor", value=ROAD_FACTOR, min_value=1.0, step=0.05)


# Validate inputs
fac_df = fac_df.dropna(subset=["Facility"]).reset_index(drop=True)
cust_df = cust_df.dropna(subset=["Customer"]).reset_index(drop=True)

if len(fac_df) == 0 or len(cust_df) == 0:
    st.warning("Add at least one facility and one customer to solve.")
    st.stop()

total_demand = cust_df["Demand (units/yr)"].sum()
total_capacity = fac_df["Capacity (units/yr)"].sum()

col1, col2, col3 = st.columns(3)
col1.metric("Candidate facilities", len(fac_df))
col2.metric("Customers", len(cust_df))
col3.metric("Capacity / Demand", f"{total_capacity:,.0f} / {total_demand:,.0f}",
            delta=f"{total_capacity - total_demand:+,.0f} slack")

if total_capacity < total_demand:
    st.error("Total capacity is below total demand — the problem is infeasible. "
             "Increase facility capacity or remove customers.")
    st.stop()

# Build network and solve
tc_df = build_transport_matrix(fac_df, cust_df, rate, road_factor)
network = make_network(fac_df, cust_df, tc_df)

with st.spinner("Solving..."):
    sol = solve(
        network,
        max_open=max_open if max_open < len(fac_df) else None,
        forced_open=forced_open,
        forced_closed=forced_closed,
    )

if sol.status != "Optimal":
    st.error(f"Solver status: {sol.status} — try relaxing the constraints.")
    st.stop()

# Results
st.divider()
st.subheader("Optimal network")
m1, m2, m3, m4 = st.columns(4)
m1.metric("Total annual cost", f"${sol.total_cost:,.0f}")
m2.metric("Fixed cost", f"${sol.fixed_cost:,.0f}",
          delta=f"{sol.fixed_cost / sol.total_cost:.0%} of total")
m3.metric("Transport cost", f"${sol.transport_cost:,.0f}",
          delta=f"{sol.transport_cost / sol.total_cost:.0%} of total")
m4.metric("Open facilities", f"{len(sol.open_facilities)} / {len(fac_df)}")

map_col, table_col = st.columns([1.5, 1])
with map_col:
    fmap = render_map(fac_df, cust_df, sol)
    st_folium(fmap, height=520, use_container_width=True, returned_objects=[])

with table_col:
    st.markdown("**Open facilities**")
    open_df = sol.facility_load[sol.facility_load["open"]].copy()
    open_df["utilization"] = open_df["utilization"].map("{:.0%}".format)
    open_df["fixed_cost"] = open_df["fixed_cost"].map("${:,.0f}".format)
    st.dataframe(
        open_df[["facility", "served_demand", "capacity", "utilization", "fixed_cost"]],
        hide_index=True, use_container_width=True,
    )

with st.expander("Customer assignments"):
    st.dataframe(
        sol.assignments[["customer", "facility", "demand", "unit_transport_cost", "transport_cost"]],
        hide_index=True, use_container_width=True,
    )

with st.expander("Transport cost matrix ($ / unit)"):
    st.dataframe(tc_df.style.format("${:.2f}").background_gradient(cmap="YlOrRd"),
                 use_container_width=True)

# Export
buf = io.BytesIO()
with pd.ExcelWriter(buf, engine="openpyxl") as writer:
    sol.assignments.to_excel(writer, sheet_name="Assignments", index=False)
    sol.facility_load.to_excel(writer, sheet_name="Facility Load", index=False)
    pd.DataFrame({
        "metric": ["Total cost", "Fixed cost", "Transport cost", "Open facilities", "Solve time (ms)"],
        "value": [sol.total_cost, sol.fixed_cost, sol.transport_cost,
                  ", ".join(sol.open_facilities), sol.solve_seconds * 1000],
    }).to_excel(writer, sheet_name="Summary", index=False)
buf.seek(0)
st.download_button("Download solution as Excel", buf,
                   file_name="cflp_solution.xlsx",
                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

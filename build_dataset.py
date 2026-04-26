"""Build the example facility_data.xlsx workbook.

Generates a realistic Capacitated Facility Location Problem instance for the
US Southeast: 7 candidate facility cities, 10 demand cities, with annual
fixed costs, capacities, demand, and an estimated transport-cost matrix
derived from great-circle distances.

Run once to (re)create facility_data.xlsx, then the notebook / dashboard
read it. Replace the city lists and constants below to model a different
network.
"""

from pathlib import Path

import pandas as pd
from geopy.distance import geodesic


# ---------------------------------------------------------------------------
# Network definition
# ---------------------------------------------------------------------------

# 7 candidate distribution-centre cities in the US Southeast.
# Fixed cost = annual operating cost ($/year) — scales with city size & rent.
# Capacity   = max units served per year.
FACILITIES = [
    {"name": "Charlotte",   "lat": 35.2272, "lon": -80.8431, "fixed_cost": 130_000, "capacity": 200},
    {"name": "Jacksonville","lat": 30.3322, "lon": -81.6557, "fixed_cost": 125_000, "capacity": 180},
    {"name": "Miami",       "lat": 25.7820, "lon": -80.2226, "fixed_cost": 145_000, "capacity": 220},
    {"name": "Atlanta",     "lat": 33.7490, "lon": -84.3902, "fixed_cost": 150_000, "capacity": 250},
    {"name": "Memphis",     "lat": 35.1460, "lon": -90.0518, "fixed_cost": 120_000, "capacity": 160},
    {"name": "Charleston",  "lat": 32.7884, "lon": -79.9399, "fixed_cost":  95_000, "capacity": 130},
    {"name": "Birmingham",  "lat": 33.5207, "lon": -86.8024, "fixed_cost": 115_000, "capacity": 150},
]

# 10 customer demand cities in the same region.
# Demand     = annual units required.
CUSTOMERS = [
    {"name": "Nashville",    "lat": 36.1627, "lon": -86.7816, "demand":  90},
    {"name": "Tampa",        "lat": 27.9506, "lon": -82.4572, "demand": 110},
    {"name": "Orlando",      "lat": 28.5384, "lon": -81.3789, "demand":  95},
    {"name": "Raleigh",      "lat": 35.7796, "lon": -78.6382, "demand":  85},
    {"name": "Savannah",     "lat": 32.0809, "lon": -81.0912, "demand":  70},
    {"name": "Columbia",     "lat": 34.0007, "lon": -81.0348, "demand":  65},
    {"name": "Mobile",       "lat": 30.6954, "lon": -88.0399, "demand":  75},
    {"name": "Knoxville",    "lat": 35.9606, "lon": -83.9207, "demand":  60},
    {"name": "Tallahassee",  "lat": 30.4383, "lon": -84.2807, "demand":  55},
    {"name": "Greenville",   "lat": 34.8526, "lon": -82.3940, "demand":  80},
]

# Cost per unit per kilometre of road distance (approximation: 1.3 × great-circle).
TRANSPORT_RATE_USD_PER_UNIT_KM = 0.05
DISTANCE_ROAD_FACTOR = 1.3


def transport_cost(facility, customer) -> float:
    gc_km = geodesic((facility["lat"], facility["lon"]), (customer["lat"], customer["lon"])).km
    return round(gc_km * DISTANCE_ROAD_FACTOR * TRANSPORT_RATE_USD_PER_UNIT_KM, 2)


def main(out_path: str = "facility_data.xlsx") -> None:
    fac_df = pd.DataFrame(FACILITIES).rename(columns={
        "name": "Facility", "lat": "Latitude", "lon": "Longitude",
        "fixed_cost": "Fixed Cost ($/yr)", "capacity": "Capacity (units/yr)",
    })

    cust_df = pd.DataFrame(CUSTOMERS).rename(columns={
        "name": "Customer", "lat": "Latitude", "lon": "Longitude",
        "demand": "Demand (units/yr)",
    })

    # Transport-cost matrix: rows = facilities, columns = customers
    tc_matrix = []
    for f in FACILITIES:
        row = {"Facility": f["name"]}
        for c in CUSTOMERS:
            row[c["name"]] = transport_cost(f, c)
        tc_matrix.append(row)
    tc_df = pd.DataFrame(tc_matrix)

    out = Path(out_path)
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        fac_df.to_excel(writer, sheet_name="Facilities", index=False)
        cust_df.to_excel(writer, sheet_name="Customers", index=False)
        tc_df.to_excel(writer, sheet_name="Transport Costs", index=False)

    print(f"Wrote {out_path}")
    print(f"  Total capacity:  {fac_df['Capacity (units/yr)'].sum():,} units/yr")
    print(f"  Total demand:    {cust_df['Demand (units/yr)'].sum():,} units/yr")
    print(f"  Network: {len(fac_df)} candidate facilities, {len(cust_df)} customers")


if __name__ == "__main__":
    main()

"""Capacitated Facility Location Problem (CFLP) — solver and helpers.

Single-sourcing CFLP formulated as a mixed-integer linear program and solved
with PuLP (CBC backend, free, no licence). The model is reusable: build a
`Network` from any facility / customer / transport-cost data and call
`solve()` to get the optimal open-set, customer assignments, and total cost.

Decision variables
------------------
y_i : binary — open facility i
x_ij: binary — customer j is fully served by facility i (single-sourcing)

Objective
---------
min  Σ_i  fixed_cost_i · y_i  +  Σ_i Σ_j  transport_cost_ij · demand_j · x_ij

Constraints
-----------
1. Each customer assigned to exactly one facility:
     Σ_i x_ij = 1  ∀ j
2. Capacity (also enforces logical x_ij ≤ y_i):
     Σ_j demand_j · x_ij ≤ capacity_i · y_i  ∀ i
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List

import pandas as pd
import pulp


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------


@dataclass
class Facility:
    name: str
    fixed_cost: float
    capacity: float
    lat: float | None = None
    lon: float | None = None


@dataclass
class Customer:
    name: str
    demand: float
    lat: float | None = None
    lon: float | None = None


@dataclass
class Network:
    facilities: List[Facility]
    customers: List[Customer]
    transport_cost: pd.DataFrame  # rows = facility names, cols = customer names

    @classmethod
    def from_excel(cls, path: str | Path) -> "Network":
        path = Path(path)
        fac_df = pd.read_excel(path, sheet_name="Facilities")
        cust_df = pd.read_excel(path, sheet_name="Customers")
        tc_df = pd.read_excel(path, sheet_name="Transport Costs").set_index("Facility")

        facilities = [
            Facility(
                name=row["Facility"],
                fixed_cost=float(row["Fixed Cost ($/yr)"]),
                capacity=float(row["Capacity (units/yr)"]),
                lat=row.get("Latitude"),
                lon=row.get("Longitude"),
            )
            for _, row in fac_df.iterrows()
        ]
        customers = [
            Customer(
                name=row["Customer"],
                demand=float(row["Demand (units/yr)"]),
                lat=row.get("Latitude"),
                lon=row.get("Longitude"),
            )
            for _, row in cust_df.iterrows()
        ]
        return cls(facilities=facilities, customers=customers, transport_cost=tc_df)


# ---------------------------------------------------------------------------
# Solver
# ---------------------------------------------------------------------------


@dataclass
class Solution:
    status: str
    total_cost: float
    fixed_cost: float
    transport_cost: float
    open_facilities: List[str]
    assignments: pd.DataFrame  # columns: customer, facility, demand, transport_cost
    facility_load: pd.DataFrame  # columns: facility, served_demand, capacity, utilization
    solve_seconds: float = 0.0


def solve(
    network: Network,
    *,
    max_open: int | None = None,
    forced_open: List[str] | None = None,
    forced_closed: List[str] | None = None,
    time_limit_seconds: int = 60,
) -> Solution:
    """Solve the CFLP for `network`.

    Parameters
    ----------
    max_open
        If set, restricts the number of facilities that can be opened.
    forced_open
        Facility names that must be opened (e.g. existing leased sites).
    forced_closed
        Facility names that must remain closed.
    time_limit_seconds
        CBC wall-clock cap; CFLP solves in milliseconds at this scale, but
        the cap protects against pathological inputs.
    """
    facilities = network.facilities
    customers = network.customers
    tc = network.transport_cost
    forced_open = forced_open or []
    forced_closed = forced_closed or []

    fac_names = [f.name for f in facilities]
    cust_names = [c.name for c in customers]

    cap = {f.name: f.capacity for f in facilities}
    fixed = {f.name: f.fixed_cost for f in facilities}
    demand = {c.name: c.demand for c in customers}

    model = pulp.LpProblem("CFLP", pulp.LpMinimize)

    y = {f: pulp.LpVariable(f"open_{f}", cat="Binary") for f in fac_names}
    x = {
        (f, c): pulp.LpVariable(f"assign_{f}_{c}", cat="Binary")
        for f in fac_names for c in cust_names
    }

    # Objective: fixed + transport (transport_cost is $/unit; multiply by demand)
    model += (
        pulp.lpSum(fixed[f] * y[f] for f in fac_names)
        + pulp.lpSum(tc.loc[f, c] * demand[c] * x[(f, c)]
                     for f in fac_names for c in cust_names),
        "total_cost",
    )

    # 1. Each customer served exactly once
    for c in cust_names:
        model += pulp.lpSum(x[(f, c)] for f in fac_names) == 1, f"serve_{c}"

    # 2. Capacity (also makes x → 0 when y = 0)
    for f in fac_names:
        model += (
            pulp.lpSum(demand[c] * x[(f, c)] for c in cust_names) <= cap[f] * y[f],
            f"cap_{f}",
        )

    # 3. Tighter logical: x_ij ≤ y_i. Improves LP relaxation noticeably.
    for f in fac_names:
        for c in cust_names:
            model += x[(f, c)] <= y[f], f"link_{f}_{c}"

    # Optional managerial constraints
    if max_open is not None:
        model += pulp.lpSum(y[f] for f in fac_names) <= max_open, "max_open"
    for f in forced_open:
        model += y[f] == 1, f"force_open_{f}"
    for f in forced_closed:
        model += y[f] == 0, f"force_close_{f}"

    solver = pulp.PULP_CBC_CMD(msg=False, timeLimit=time_limit_seconds)
    import time
    t0 = time.perf_counter()
    model.solve(solver)
    elapsed = time.perf_counter() - t0

    status = pulp.LpStatus[model.status]
    if status != "Optimal":
        return Solution(
            status=status, total_cost=float("nan"), fixed_cost=float("nan"),
            transport_cost=float("nan"), open_facilities=[],
            assignments=pd.DataFrame(),
            facility_load=pd.DataFrame(),
            solve_seconds=elapsed,
        )

    open_set = [f for f in fac_names if y[f].value() > 0.5]
    rows = []
    for c in cust_names:
        for f in fac_names:
            if x[(f, c)].value() > 0.5:
                rows.append({
                    "customer": c, "facility": f,
                    "demand": demand[c],
                    "unit_transport_cost": float(tc.loc[f, c]),
                    "transport_cost": float(tc.loc[f, c]) * demand[c],
                })
                break
    assignments = pd.DataFrame(rows)

    load_rows = []
    for f in fac_names:
        served = assignments.loc[assignments["facility"] == f, "demand"].sum()
        load_rows.append({
            "facility": f,
            "open": f in open_set,
            "capacity": cap[f],
            "served_demand": served,
            "utilization": served / cap[f] if cap[f] else 0,
            "fixed_cost": fixed[f] if f in open_set else 0,
        })
    facility_load = pd.DataFrame(load_rows)

    fixed_total = sum(fixed[f] for f in open_set)
    transport_total = float(assignments["transport_cost"].sum())

    return Solution(
        status="Optimal",
        total_cost=fixed_total + transport_total,
        fixed_cost=fixed_total,
        transport_cost=transport_total,
        open_facilities=open_set,
        assignments=assignments,
        facility_load=facility_load,
        solve_seconds=elapsed,
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    net = Network.from_excel("facility_data.xlsx")
    sol = solve(net)
    print(f"Status: {sol.status}  ({sol.solve_seconds*1000:.0f} ms)")
    print(f"Total cost:     ${sol.total_cost:>12,.0f}")
    print(f"  Fixed:        ${sol.fixed_cost:>12,.0f}")
    print(f"  Transport:    ${sol.transport_cost:>12,.0f}")
    print(f"\nOpen facilities ({len(sol.open_facilities)}): {sol.open_facilities}")
    print("\nAssignments:")
    print(sol.assignments.to_string(index=False))
    print("\nFacility load:")
    print(sol.facility_load.to_string(index=False))

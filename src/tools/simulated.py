"""SIMULATED business APIs: order search/details, shipment lookup, employee DB.

These are deterministic stand-ins for real systems. They read controlled
fixtures (``data/fixtures/orders.json`` etc.), validate their parameters, can
be made to fail through the :class:`FaultInjector`, and mark every result with
``simulated: true``. Nothing here contacts a real service.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import Field

from ..condition_evaluator import norm_text
from ..errors import ToolExecutionError, ToolInputError
from ..tool_registry import ToolContext, ToolRegistry, ToolSpec
from .common import In, Out, Row
from .data_access import to_number

SIM_NOTE = "SIMULATED: results come from local fixture data, not a live system."


def _load_json(ctx: ToolContext, name: str) -> dict:
    path = ctx.settings.fixtures_dir / name
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ToolExecutionError("The simulated data source is unavailable.", detail=f"missing {path}") from exc
    except json.JSONDecodeError as exc:
        raise ToolExecutionError("The simulated data source is corrupt.", detail=str(exc)) from exc


# ----------------------------------------------------------------- order_search
class OrderSearchIn(In):
    kind: Literal["order_id", "email"]
    value: str


class OrderMatch(Out):
    order_id: str
    customer_email: str


class OrderSearchOut(Out):
    matches: list[OrderMatch]
    order_ids: list[str]
    count: int
    simulated: bool
    note: str


def order_search(p: OrderSearchIn, ctx: ToolContext) -> dict:
    ctx.faults.check("order_search")
    if not p.value.strip():
        raise ToolInputError("An order ID or email is required.", code="identifier_missing")
    orders = _load_json(ctx, "orders.json")["orders"]
    needle = p.value.strip()
    if p.kind == "order_id":
        hits = [o for o in orders if o["order_id"].upper() == needle.upper()]
    else:
        hits = [o for o in orders if o["customer_email"].lower() == needle.lower()]
    matches = [{"order_id": o["order_id"], "customer_email": o["customer_email"]} for o in hits]
    return {"matches": matches, "order_ids": [m["order_id"] for m in matches], "count": len(matches), "simulated": True, "note": SIM_NOTE}


# ---------------------------------------------------------------- order_details
class OrderDetailsIn(In):
    order_ids: list[str]


class OrderDetailsOut(Out):
    orders: list[Row]
    not_found: list[str]
    count: int
    simulated: bool
    note: str


def order_details(p: OrderDetailsIn, ctx: ToolContext) -> dict:
    ctx.faults.check("order_details")
    by_id = {o["order_id"]: o for o in _load_json(ctx, "orders.json")["orders"]}
    found, missing = [], []
    for oid in p.order_ids:
        o = by_id.get(oid)
        if o is None:
            missing.append(oid)
            continue
        found.append({
            "order_id": o["order_id"], "status": o["status"], "order_date": o["order_date"],
            "customer_name": o["customer_name"], "total": o["total"], "currency": o["currency"],
            "item_count": sum(i["quantity"] for i in o["items"]),
            "items": ", ".join(f"{i['quantity']} x {i['name']}" for i in o["items"]),
        })
    return {"orders": found, "not_found": missing, "count": len(found), "simulated": True, "note": SIM_NOTE}


# -------------------------------------------------------------- shipment_lookup
class ShipmentLookupIn(In):
    order_ids: list[str]


class ShipmentLookupOut(Out):
    shipments: list[Row]
    without_shipment: list[str]
    count: int
    simulated: bool
    note: str


def shipment_lookup(p: ShipmentLookupIn, ctx: ToolContext) -> dict:
    ctx.faults.check("shipment_lookup")
    by_id = {s["order_id"]: s for s in _load_json(ctx, "shipments.json")["shipments"]}
    shipments = [dict(by_id[o]) for o in p.order_ids if o in by_id]
    missing = [o for o in p.order_ids if o not in by_id]
    return {"shipments": shipments, "without_shipment": missing, "count": len(shipments), "simulated": True, "note": SIM_NOTE}


# -------------------------------------------------------------------- employee_db
class EmployeeDbIn(In):
    entity: Literal["employees", "open_tasks"]
    filters: dict[str, str | None] = Field(default_factory=dict, description="case-insensitive equality filters (blank values are ignored)")


class EmployeeDbOut(Out):
    rows: list[Row]
    count: int
    entity: str
    simulated: bool
    note: str


def employee_db(p: EmployeeDbIn, ctx: ToolContext) -> dict:
    ctx.faults.check("employee_db")
    file = "employees.csv" if p.entity == "employees" else "open_tasks.csv"
    _, raw, _ = ctx.data.read_table(file)
    rows: list[Row] = []
    for r in raw:
        r = {k: v for k, v in r.items() if not k.startswith("_")}
        if p.entity == "employees":
            r["skills"] = [s.strip().lower() for s in (r.get("skills") or "").split(";") if s.strip()]
            r["weekly_capacity_hours"] = to_number(r.get("weekly_capacity_hours"))
        else:
            r["estimated_hours"] = to_number(r.get("estimated_hours"))
        rows.append(r)
    if p.entity == "open_tasks" and "status" not in p.filters:
        rows = [r for r in rows if norm_text(r.get("status")) == "open"]
    for key, wanted in p.filters.items():
        if wanted is None or not str(wanted).strip():
            continue
        if rows and key not in rows[0]:
            raise ToolInputError(f"Unknown filter field '{key}' for {p.entity}.", code="unknown_filter")
        rows = [r for r in rows if norm_text(r.get(key)) == norm_text(wanted)]
    return {"rows": rows, "count": len(rows), "entity": p.entity, "simulated": True, "note": SIM_NOTE}


def register(reg: ToolRegistry) -> None:
    kw: dict[str, Any] = dict(kind="simulated_api", simulated=True)
    reg.register(ToolSpec("order_search", "SIMULATED order system: find orders by order ID or customer email.",
                          OrderSearchIn, OrderSearchOut, order_search, **kw))
    reg.register(ToolSpec("order_details", "SIMULATED order system: status, items and totals for order IDs.",
                          OrderDetailsIn, OrderDetailsOut, order_details, **kw))
    reg.register(ToolSpec("shipment_lookup", "SIMULATED carrier API: shipment and tracking info for order IDs.",
                          ShipmentLookupIn, ShipmentLookupOut, shipment_lookup, **kw))
    reg.register(ToolSpec("employee_db", "SIMULATED HR/task database: employees (skills, capacity) or open tasks.",
                          EmployeeDbIn, EmployeeDbOut, employee_db, **kw))

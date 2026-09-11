"""Best disjoint one-for-one swaps under provisional cell-count assumptions."""
from __future__ import annotations

from ortools.sat.python import cp_model


def choose_swaps(ground, low):
    model = cp_model.CpModel()
    pairs = []
    for incoming in ground:
        if not incoming.get("comparable", incoming.get("eligible", False)):
            continue
        for outgoing in low:
            gain = incoming["total"] - outgoing["total"]
            if outgoing["cells"] < incoming["cells"] or gain <= 0:
                continue
            variable = model.NewBoolVar(f"swap_{len(pairs)}")
            pairs.append((incoming, outgoing, gain, variable))
    if not pairs:
        return [], "no_positive_pairs"
    for incoming in ground:
        model.Add(sum(v for a, _b, _gain, v in pairs if a["key"] == incoming["key"]) <= 1)
    for outgoing in low:
        model.Add(sum(v for _a, b, _gain, v in pairs if b["key"] == outgoing["key"]) <= 1)
    model.Maximize(sum(gain * variable for _a, _b, gain, variable in pairs))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = .5
    solver.parameters.num_search_workers = 1
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return [], "timeout"
    swaps = [{"take_key": a["key"], "replace_key": b["key"], "gain": gain,
              "status": "conditional", "condition": "按参考价和占格数测算；实际形状、空位需满足"}
             for a, b, gain, v in pairs if solver.Value(v)]
    return sorted(swaps, key=lambda p: -p["gain"]), (
        "optimal_one_for_one_model" if status == cp_model.OPTIMAL else "feasible_one_for_one_model")


def build_advice(session: dict, metadata: dict) -> dict:
    rows = [row for row in session["rows"] if not row["excluded"]]
    ground = sorted([r for r in rows if r["scope"] == "loot" and r["total"] is not None],
                    key=lambda r: (-r["per_cell"], -r["total"]))
    keep = [r for r in rows if r["scope"] != "loot" and (
        r["locked"] or r["scope"] in {"safe_box", "carried"} or (
            r["definition"] and r["definition"]["category"] == "weapon" and
            r["manual_total"] is None))]
    low = sorted([r for r in rows if r["scope"] == "backpack" and
                  r.get("comparable", r.get("eligible", False))
                  and not r["locked"]], key=lambda r: (r["per_cell"], r["total"]))
    swaps, solve_status = choose_swaps(ground, low)
    warnings = ["按当前显示的参考价比较，不重复使用同一件物品；"
                "仅计算一换一，不代表完整背包全局最优。"]
    if metadata["stale"]:
        warnings.append("价格快照已超过 24 小时，差价仅供参考。")
    return {"ground_keys": [r["key"] for r in ground[:10]],
            "keep_keys": [r["key"] for r in keep],
            "low_keys": [r["key"] for r in low[:10]],
            "pending_keys": [], "swaps": swaps,
            "estimated_gain": sum(p["gain"] for p in swaps), "solve_status": solve_status,
            "warnings": warnings, "scope": "all", "status": "reference_only"}

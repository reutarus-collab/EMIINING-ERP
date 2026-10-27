import math

import numpy as np
from scipy.optimize import linprog

from services.db import db
from services.models import AnimalRequirement, FeedIngredient, LocationStock


def _finite(value, default=0.0):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _best_possible(objective, bounds, count):
    """Best value of one nutrient on its own (ignoring the other targets), or None."""
    result = linprog(c=objective, A_eq=np.ones((1, count)), b_eq=np.array([1.0]), bounds=bounds, method='highs')
    return float(result.fun) if result.success else None


def solve_feed_formulation(species_stage, target_batch_kg=1000.0,
                           ingredient_ids=None, location_id=None,
                           target_cp_pct=None, target_me_mcal=None,
                           target_fiber_max_pct=None, limit_to_stock=False, extra=None):
    """Least-cost mix of raw ingredients that meets minimum protein and energy and
    maximum fibre, respects each ingredient's safe inclusion limit, and (optionally)
    only uses what is physically in stock at the outlet."""
    try:
        target_batch_kg = float(target_batch_kg)
    except (TypeError, ValueError):
        return {"status": "error", "message": "Batch size must be a number above zero."}
    if not math.isfinite(target_batch_kg) or target_batch_kg <= 0:
        return {"status": "error", "message": "Batch size must be a number above zero."}

    req = AnimalRequirement.query.filter_by(species_stage=species_stage).first()
    min_cp = float(target_cp_pct if target_cp_pct is not None else (req.min_cp if req else 16.0) or 16.0)
    min_me = float(target_me_mcal if target_me_mcal is not None else (req.min_me if req else 2.5) or 2.5)
    max_fiber = target_fiber_max_pct if target_fiber_max_pct is not None else (req.max_fiber if req else 0.0)
    max_fiber = _finite(max_fiber, -1.0)
    if not math.isfinite(min_cp) or not 0 < min_cp <= 100:
        return {"status": "error", "message": "Crude protein target must be above 0 and at most 100%."}
    if not math.isfinite(min_me) or min_me <= 0:
        return {"status": "error", "message": "Metabolizable energy target must be above 0 Mcal/kg."}
    if max_fiber < 0 or max_fiber > 100:
        return {"status": "error", "message": "Maximum fibre must be between 0 and 100%. Use 0 for no limit."}

    # Optional nutrient limits (percent of the finished feed). 0 / missing = not enforced.
    extra = dict(extra or {})
    ex = {}
    for key in ('min_lysine', 'min_methionine', 'min_calcium', 'max_calcium', 'min_phosphorus'):
        raw = extra.get(key)
        ex[key] = _finite(raw if raw not in (None, '') else (getattr(req, key, 0.0) if req else 0.0), -1.0)
        if ex[key] < 0 or ex[key] > 100:
            return {"status": "error", "message": "Lysine, methionine, calcium and phosphorus limits must be between 0 and 100% (0 = not enforced)."}
    if ex['max_calcium'] > 0 and ex['min_calcium'] > ex['max_calcium']:
        return {"status": "error", "message": "Minimum calcium cannot be above maximum calcium."}

    query = FeedIngredient.query.filter(FeedIngredient.category.ilike('Raw%'))
    if ingredient_ids:
        query = query.filter(FeedIngredient.id.in_(ingredient_ids))
    ingredients = query.order_by(db.func.lower(FeedIngredient.name)).all()
    if not ingredients:
        return {"status": "error", "message": "No raw ingredients match this formulation."}

    stocks = {}
    if location_id is not None:
        stocks = {row.ingredient_id: row for row in LocationStock.query.filter(
            LocationStock.location_id == location_id,
            LocationStock.ingredient_id.in_([i.id for i in ingredients]),
        ).all()}

    usable, costs, cp, me, fiber, upper = [], [], [], [], [], []
    ca, phos, lys, met = [], [], [], []
    no_cost, no_nutrients = [], []
    for ingredient in ingredients:
        stock = stocks.get(ingredient.id)
        outlet_cost = stock.unit_cost_per_kg if stock else None
        cost = float(outlet_cost if outlet_cost and outlet_cost > 0 else ingredient.cost_per_kg or 0.0)
        protein = float(ingredient.crude_protein_pct or 0.0)
        energy = float(ingredient.metabolizable_energy_mcal or 0.0)
        fibre = float(ingredient.crude_fiber_pct or 0.0)
        extra_vals = [float(getattr(ingredient, a, 0.0) or 0.0) for a in
                      ('calcium_pct', 'phosphorus_pct', 'lysine_pct', 'methionine_pct')]
        if not all(math.isfinite(v) and 0 <= v <= 100 for v in extra_vals):
            return {"status": "error", "message": f"Invalid calcium, phosphorus, lysine or methionine value for {ingredient.name}."}
        limit = ingredient.max_inclusion_pct
        limit = 100.0 if limit is None else float(limit)
        if not all(math.isfinite(v) and v >= 0 for v in (cost, protein, energy, fibre, limit)) or limit > 100:
            return {"status": "error", "message": f"Invalid cost, nutrient or inclusion-limit data for {ingredient.name}."}
        if cost <= 0:
            no_cost.append(ingredient.name)
            continue
        if protein == 0 and energy == 0:
            no_nutrients.append(ingredient.name)
        ub = limit / 100.0
        if limit_to_stock:
            on_hand = max(0.0, float((stock.quantity_kg or 0.0) - (stock.reserved_quantity_kg or 0.0))) if stock else 0.0
            ub = min(ub, on_hand / target_batch_kg)
        usable.append(ingredient)
        costs.append(cost)
        cp.append(protein)
        me.append(energy)
        fiber.append(fibre)
        ca.append(extra_vals[0]); phos.append(extra_vals[1]); lys.append(extra_vals[2]); met.append(extra_vals[3])
        upper.append(ub)

    ingredients = usable
    if not ingredients:
        return {"status": "error", "message": "Set a positive unit cost for at least one raw ingredient at this outlet before formulating."}

    warnings = []
    if no_cost:
        warnings.append("Left out (no cost set): " + ", ".join(no_cost) + ".")
    if no_nutrients:
        warnings.append("No protein or energy value entered, so they cannot help meet targets: " + ", ".join(no_nutrients) + ".")

    # Composition values are percentages, energy is Mcal/kg; fractions sum to 1.
    A_ub = [-np.asarray(cp, dtype=float), -np.asarray(me, dtype=float)]
    b_ub = [-min_cp, -min_me]
    if max_fiber > 0:
        A_ub.append(np.asarray(fiber, dtype=float))
        b_ub.append(max_fiber)
    # (label, per-ingredient values, 'min'|'max', limit, unit). Only enforced when the limit is above 0.
    extra_rules = [
        ('lysine', lys, 'min', ex['min_lysine'], '%'),
        ('methionine', met, 'min', ex['min_methionine'], '%'),
        ('calcium', ca, 'min', ex['min_calcium'], '%'),
        ('calcium', ca, 'max', ex['max_calcium'], '%'),
        ('available phosphorus', phos, 'min', ex['min_phosphorus'], '%'),
    ]
    extra_rules = [r for r in extra_rules if r[3] > 0]
    for label, vals, sense, limit, unit in extra_rules:
        arr = np.asarray(vals, dtype=float)
        A_ub.append(-arr if sense == 'min' else arr)
        b_ub.append(-limit if sense == 'min' else limit)
        if not any(v > 0 for v in vals):
            warnings.append(f"No ingredient has a {label} value entered, so the {label} {sense}imum cannot be checked or met. Fill it in under Purchasing > Item Master > Edit nutrients.")
    bounds = [(0, ub) for ub in upper]
    result = linprog(
        c=np.asarray(costs, dtype=float),
        A_ub=np.asarray(A_ub, dtype=float), b_ub=np.asarray(b_ub, dtype=float),
        A_eq=np.ones((1, len(ingredients)), dtype=float), b_eq=np.array([1.0]),
        bounds=bounds, method='highs',
    )
    if not result.success:
        n = len(ingredients)
        diagnosis = {}
        if sum(upper) < 1.0 - 1e-9:
            diagnosis["note"] = "The selected ingredients, their inclusion limits" + (" and stock on hand" if limit_to_stock else "") + \
                f" add up to only {sum(upper) * 100:.1f}% of a batch. They cannot fill it."
        else:
            best_cp = _best_possible(-np.asarray(cp, dtype=float), bounds, n)
            best_me = _best_possible(-np.asarray(me, dtype=float), bounds, n)
            low_fiber = _best_possible(np.asarray(fiber, dtype=float), bounds, n)
            diagnosis = {"best_crude_protein_pct": round(-best_cp, 2) if best_cp is not None else None,
                         "best_metabolizable_energy_mcal": round(-best_me, 3) if best_me is not None else None,
                         "lowest_crude_fiber_pct": round(low_fiber, 2) if low_fiber is not None else None}
            parts = []
            if best_cp is not None and -best_cp < min_cp - 1e-9:
                parts.append(f"best possible protein is {-best_cp:.2f}% (need {min_cp:.2f}%)")
            if best_me is not None and -best_me < min_me - 1e-9:
                parts.append(f"best possible energy is {-best_me:.2f} Mcal/kg (need {min_me:.2f})")
            if max_fiber > 0 and low_fiber is not None and low_fiber > max_fiber + 1e-9:
                parts.append(f"lowest possible fibre is {low_fiber:.2f}% (limit {max_fiber:.2f}%)")
            for label, vals, sense, limit, unit in extra_rules:
                arr = np.asarray(vals, dtype=float)
                best = _best_possible(-arr if sense == 'min' else arr, bounds, n)
                if best is None:
                    continue
                best = -best if sense == 'min' else best
                if sense == 'min' and best < limit - 1e-9:
                    parts.append(f"best possible {label} is {best:.2f}% (need at least {limit:.2f}%)")
                elif sense == 'max' and best > limit + 1e-9:
                    parts.append(f"lowest possible {label} is {best:.2f}% (limit {limit:.2f}%)")
            diagnosis["note"] = ("; ".join(parts).capitalize() + ".") if parts else \
                "Each target can be met on its own, but not all together. Protein, energy and fibre pull against each other with these ingredients and limits."
        return {"status": "infeasible",
                "message": "These ingredients cannot meet the targets. " + diagnosis["note"],
                "diagnosis": diagnosis, "warnings": warnings}

    recipe, total_cost = [], 0.0
    achieved = {"cp": 0.0, "me": 0.0, "fiber": 0.0, "ca": 0.0, "p": 0.0, "lys": 0.0, "met": 0.0}
    for index, ingredient in enumerate(ingredients):
        fraction = float(result.x[index])
        if fraction <= 0.00001:
            continue
        kg = fraction * target_batch_kg
        line_cost = kg * costs[index]
        stock = stocks.get(ingredient.id)
        achieved["cp"] += fraction * cp[index]
        achieved["me"] += fraction * me[index]
        achieved["fiber"] += fraction * fiber[index]
        achieved["ca"] += fraction * ca[index]
        achieved["p"] += fraction * phos[index]
        achieved["lys"] += fraction * lys[index]
        achieved["met"] += fraction * met[index]
        recipe.append({
            "ingredient_id": ingredient.id,
            "ingredient_name": ingredient.name,
            "fraction": round(fraction * 100, 2),
            "kg_required": round(kg, 2),
            "available_kg": round(float(stock.quantity_kg or 0.0), 2) if stock else 0.0,
            "cost_per_kg": round(costs[index], 2),
            "cost": round(line_cost, 2),
        })
        total_cost += line_cost
    short = [r["ingredient_name"] for r in recipe if r["kg_required"] > r["available_kg"] + 0.005]
    if short and not limit_to_stock:
        warnings.append("Not enough stock at this outlet for this batch: " + ", ".join(short) + ".")

    return {
        "status": "optimal",
        "species_stage": species_stage,
        "target_batch_kg": target_batch_kg,
        "target_crude_protein_pct": min_cp,
        "target_metabolizable_energy_mcal": min_me,
        "target_max_crude_fiber_pct": max_fiber if max_fiber > 0 else None,
        "limit_to_stock": bool(limit_to_stock),
        "achieved": {"crude_protein_pct": round(achieved["cp"], 2),
                     "metabolizable_energy_mcal": round(achieved["me"], 3),
                     "crude_fiber_pct": round(achieved["fiber"], 2),
                     "calcium_pct": round(achieved["ca"], 3), "phosphorus_pct": round(achieved["p"], 3),
                     "lysine_pct": round(achieved["lys"], 3), "methionine_pct": round(achieved["met"], 3)},
        "extra_targets": ex,
        "cost_per_kg": round(float(result.fun), 3),
        "total_batch_cost": round(float(total_cost), 2),
        "recipe": recipe,
        "warnings": warnings,
    }

import math

import numpy as np
from scipy.optimize import linprog

from services.models import AnimalRequirement, FeedIngredient, LocationStock


def solve_feed_formulation(species_stage, target_batch_kg=1000.0,
                           ingredient_ids=None, location_id=None,
                           target_cp_pct=None, target_me_mcal=None):
    """Find a lowest-cost raw-ingredient mix using the selected outlet's costs."""
    try:
        target_batch_kg = float(target_batch_kg)
    except (TypeError, ValueError):
        return {"status": "error", "message": "Batch size must be a number above zero."}
    if not math.isfinite(target_batch_kg) or target_batch_kg <= 0:
        return {"status": "error", "message": "Batch size must be a number above zero."}

    req = AnimalRequirement.query.filter_by(species_stage=species_stage).first()
    min_cp = float(target_cp_pct if target_cp_pct is not None else (req.min_cp if req else 16.0) or 16.0)
    min_me = float(target_me_mcal if target_me_mcal is not None else (req.min_me if req else 2.5) or 2.5)
    if not math.isfinite(min_cp) or not 0 < min_cp <= 100:
        return {"status": "error", "message": "Crude protein target must be above 0 and at most 100%."}
    if not math.isfinite(min_me) or min_me <= 0:
        return {"status": "error", "message": "Metabolizable energy target must be above 0 Mcal/kg."}

    query = FeedIngredient.query.filter(FeedIngredient.category.ilike('Raw%'))
    if ingredient_ids:
        query = query.filter(FeedIngredient.id.in_(ingredient_ids))
    ingredients = query.order_by(FeedIngredient.name).all()
    if not ingredients:
        return {"status": "error", "message": "No raw ingredients match this formulation."}

    stocks = {}
    if location_id is not None:
        stocks = {row.ingredient_id: row for row in LocationStock.query.filter(
            LocationStock.location_id == location_id,
            LocationStock.ingredient_id.in_([i.id for i in ingredients]),
        ).all()}

    usable_ingredients, costs, cp, me = [], [], [], []
    for ingredient in ingredients:
        stock = stocks.get(ingredient.id)
        outlet_cost = stock.unit_cost_per_kg if stock else None
        cost = float(outlet_cost if outlet_cost and outlet_cost > 0 else ingredient.cost_per_kg or 0.0)
        protein = float(ingredient.crude_protein_pct or 0.0)
        energy = float(ingredient.metabolizable_energy_mcal or 0.0)
        if not all(math.isfinite(v) and v >= 0 for v in (cost, protein, energy)):
            return {"status": "error", "message": f"Invalid cost or nutrient data for {ingredient.name}."}
        if cost <= 0:
            continue
        usable_ingredients.append(ingredient)
        costs.append(cost)
        cp.append(protein)
        me.append(energy)

    ingredients = usable_ingredients
    if not ingredients:
        return {"status": "error", "message": "Set a positive unit cost for at least one raw ingredient at this outlet before formulating."}

    # Ingredient composition values are stored as percentages, while energy is Mcal/kg.
    result = linprog(
        c=np.asarray(costs, dtype=float),
        A_ub=-np.asarray([cp, me], dtype=float),
        b_ub=-np.asarray([min_cp, min_me], dtype=float),
        A_eq=np.ones((1, len(ingredients)), dtype=float),
        b_eq=np.array([1.0]),
        bounds=[(0, 1.0)] * len(ingredients),
        method='highs',
    )
    if not result.success:
        return {"status": "infeasible", "message": "The selected ingredients cannot meet the protein and energy targets. Check ingredient nutrient values or choose more ingredients."}

    recipe, total_cost = [], 0.0
    for index, ingredient in enumerate(ingredients):
        fraction = float(result.x[index])
        if fraction <= 0.00001:
            continue
        kg = fraction * target_batch_kg
        line_cost = kg * costs[index]
        stock = stocks.get(ingredient.id)
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

    return {
        "status": "optimal",
        "species_stage": species_stage,
        "target_batch_kg": target_batch_kg,
        "target_crude_protein_pct": min_cp,
        "target_metabolizable_energy_mcal": min_me,
        "cost_per_kg": round(float(result.fun), 3),
        "total_batch_cost": round(float(total_cost), 2),
        "recipe": recipe,
    }

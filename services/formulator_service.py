import numpy as np
from scipy.optimize import linprog
from services.models import FeedIngredient, AnimalRequirement

def solve_feed_formulation(species_stage, target_batch_kg=1000.0, ingredient_ids=None):
    req = AnimalRequirement.query.filter_by(species_stage=species_stage).first()
    if not req:
        # Fallback default constraints if species not found in database
        min_cp, min_me, min_lys, min_ca, min_p = 16.0, 2.5, 0.8, 0.9, 0.45
    else:
        min_cp, min_me, min_lys, min_ca, min_p = req.min_cp, req.min_me, req.min_lysine, req.min_calcium, req.min_phosphorus

    if ingredient_ids:
        ingredients = FeedIngredient.query.filter(FeedIngredient.id.in_(ingredient_ids)).all()
    else:
        ingredients = FeedIngredient.query.all()

    if not ingredients:
        return {"status": "error", "message": "No raw feed ingredients available in inventory database."}

    num_ingredients = len(ingredients)

    # Objective Function: Cost per kg of each ingredient
    c = [ing.cost_per_kg for ing in ingredients]

    # Inequality constraints A_ub * x <= b_ub (convert >= to <= by multiplying by -1)
    A_ub = [
        [-ing.crude_protein_pct for ing in ingredients],
        [-ing.metabolizable_energy_mcal for ing in ingredients],
        [-ing.lysine_pct for ing in ingredients],
        [-ing.calcium_pct for ing in ingredients],
        [-ing.phosphorus_pct for ing in ingredients]
    ]
    b_ub = [-min_cp, -min_me, -min_lys, -min_ca, -min_p]

    # Equality constraints A_eq * x = b_eq (Sum of fractions must equal 1.0)
    A_eq = [[1.0] * num_ingredients]
    b_eq = [1.0]

    # Bounds for each ingredient fraction [0, 1]
    bounds = [(0, 1.0) for _ in range(num_ingredients)]

    res = linprog(c, A_ub=A_ub, b_ub=b_ub, A_eq=A_eq, b_eq=b_eq, bounds=bounds, method='highs')

    if res.success:
        proportions = res.x
        recipe = []
        total_cost = 0.0

        for idx, ing in enumerate(ingredients):
            kg_required = proportions[idx] * target_batch_kg
            cost = kg_required * ing.cost_per_kg
            total_cost += cost
            if kg_required > 0.001:
                recipe.append({
                    "ingredient_id": ing.id,
                    "ingredient_name": ing.name,
                    "fraction": float(proportions[idx]),
                    "kg_required": round(float(kg_required), 2),
                    "cost": round(float(cost), 2)
                })

        return {
            "status": "optimal",
            "species_stage": species_stage,
            "target_batch_kg": target_batch_kg,
            "cost_per_kg": round(float(res.fun), 3),
            "total_batch_cost": round(float(total_cost), 2),
            "recipe": recipe
        }
    else:
        return {
            "status": "infeasible",
            "message": "Infeasible formulation matrix. Adjust ingredient selection or requirement bounds."
        }
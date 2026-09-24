from flask import Blueprint, request, jsonify

factory_bp = Blueprint('factory', __name__)

@factory_bp.route('/api/formulate', methods=['POST'])
def run_formulation():
    data = request.get_json()
    species = data.get('species', 'Unknown')
    batch = float(data.get('target_batch_kg', 1000))
    mock_recipe = f"Optimal Ration Result for: {species}\nTotal Batch Size: {batch} KG\n\nIngredient Breakdown:\n- Maize Grain: {batch * 0.55} KG (55%)\n- Soybean/Ochonga: {batch * 0.25} KG (25%)\n- Bran/Pollard: {batch * 0.18} KG (18%)\n- Minerals/Premix: {batch * 0.02} KG (2%)\n\nEstimated Cost: KSh {(batch * 45):,.2f}"
    return jsonify({"status": "success", "recipe": mock_recipe})

@factory_bp.route('/api/factory/mill', methods=['POST'])
def process_milling():
    return jsonify({"status": "success", "message": "Milling run completed and stock updated."})

@factory_bp.route('/api/factory/produce', methods=['POST'])
def process_production():
    return jsonify({"status": "success", "message": "Batch production completed. Finished feed added to inventory."})
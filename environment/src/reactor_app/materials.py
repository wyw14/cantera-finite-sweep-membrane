"""Existing mechanism adapter, used by inlet inspection and simulation clients."""
from pathlib import Path
import cantera as ct

DEFAULT_MECHANISM = str(Path(__file__).resolve().parents[1] / 'upstream' /
                        'ammonia-Ru-Ba-YSZ-CSM-2019.yaml')

def load_materials(config):
    path = config.get('mechanism', DEFAULT_MECHANISM)
    gas_name = config.get('gas_phase', 'gas')
    surface_name = config.get('surface_phase', 'Ru_surface')
    try:
        gas = ct.Solution(path, gas_name, transport_model=None)
        sweep = ct.Solution(path, gas_name, transport_model=None)
        surface = ct.Interface(path, surface_name, adjacent=[gas])
    except Exception as exc:
        raise ValueError('Cannot load requested gas and catalyst phases') from exc
    if gas.thermo_model != 'ideal-gas' or surface.thermo_model != 'ideal-surface':
        raise ValueError('Only an ideal gas and ideal surface are supported')
    return gas, sweep, surface

def inspect_inlet(config, side='retentate'):
    """Existing inspector: thermodynamic properties of one specified inlet."""
    gas, _, _ = load_materials(config)
    feed = config[side]
    gas.TPX = feed['temperature_K'], feed['pressure_Pa'], feed['mole_fractions']
    flow = feed['molar_flow_kmol_s']
    return {'species': list(gas.species_names),
            'enthalpy_flow_W': float(flow * gas.enthalpy_mole),
            'mass_flow_kg_s': float(flow * gas.mean_molecular_weight)}

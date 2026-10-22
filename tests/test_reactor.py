"""Verify the public reactor contract with analytic and independent models.

The custom mechanism below is a deliberately constructed analytic fixture, not
measured process data. Test docstrings identify the corresponding requirements
in instruction.md; all expected values follow those published requirements.
"""

import copy
import json
from pathlib import Path
import numpy as np
import cantera as ct
import pytest
from scipy.optimize import brentq
from candidate_proxy import candidate, run_cli
from independent_fv import solve_cells

MECH = str(Path(__file__).resolve().parent / "data" / "ammonia-Ru-Ba-YSZ-CSM-2019.yaml")


def configuration(direction="co-current", points=81):
    """Build a supported ammonia case using the published input fields."""
    return {
        "mechanism": MECH,
        "gas_phase": "gas",
        "surface_phase": "Ru_surface",
        "length_m": 0.05,
        "catalyst_area_m2": 50.0,
        "retentate_void_volume_m3": 1e-5,
        "membrane": {"species": "H2", "area_m2": 0.02, "permeance_kmol_m2_s_Pa": 2e-10},
        "heat": {
            "interchannel_UA_W_K": 0.005,
            "wall_UA_W_K": 0.003,
            "wall_temperature_K": 720.0,
        },
        "retentate": {
            "temperature_K": 700.0,
            "pressure_Pa": 3e5,
            "molar_flow_kmol_s": 1e-6,
            "mole_fractions": {"NH3": 0.6, "H2": 0.04, "N2": 0.01, "AR": 0.35},
        },
        "sweep": {
            "temperature_K": 650.0,
            "pressure_Pa": 1e5,
            "molar_flow_kmol_s": 2e-6,
            "mole_fractions": {"H2": 0.005, "AR": 0.995},
        },
        "flow_direction": direction,
        "sample_positions_m": np.linspace(0, 0.05, points).tolist(),
    }


@pytest.fixture
def analytic_mechanism(tmp_path):
    """Create a loadable local mechanism with independently solvable limits."""
    # Purpose-built analytic verification mechanism, not measured process data.
    # A and B are equal-thermo isomers; surface conversion has a closed form.
    path = tmp_path / "analytic.yaml"
    path.write_text(
        "units: {length: m, quantity: kmol, time: s, "
        "activation-energy: J/kmol}\n"
        """phases:
- name: gas
  thermo: ideal-gas
  kinetics: gas
  elements: [H, Ar]
  species: [A, B, I]
  reactions: [gas-reactions]
- name: catalyst
  thermo: ideal-surface
  adjacent-phases: [gas]
  elements: [Pt, H]
  species: [S, AS, BS]
  kinetics: surface
  reactions: [surface-reactions]
  site-density: 1.0e-6
species:
- name: A
  composition: {H: 2}
  thermo: {model: constant-cp, T0: 300, h0: 0, s0: 0, cp0: 30000}
- name: B
  composition: {H: 2}
  thermo: {model: constant-cp, T0: 300, h0: 0, s0: 0, cp0: 30000}
- name: I
  composition: {Ar: 1}
  thermo: {model: constant-cp, T0: 300, h0: 0, s0: 0, cp0: 20000}
- name: S
  composition: {Pt: 1}
  thermo: {model: constant-cp}
- name: AS
  composition: {Pt: 1, H: 2}
  thermo: {model: constant-cp}
- name: BS
  composition: {Pt: 1, H: 2}
  thermo: {model: constant-cp}
gas-reactions:
- equation: A => B
  rate-constant: {A: 0.4, b: 0, Ea: 0}
surface-reactions:
- equation: A + S => AS
  rate-constant: {A: 200, b: 0, Ea: 0}
- equation: AS => A + S
  rate-constant: {A: 3, b: 0, Ea: 0}
- equation: AS => BS
  rate-constant: {A: 5, b: 0, Ea: 0}
- equation: BS => B + S
  rate-constant: {A: 7, b: 0, Ea: 0}
"""
    )
    return str(path)


def analytic_configuration(path, direction="co-current", points=31):
    """Disable source terms so each analytic test enables only its target term."""
    c = configuration(direction, points)
    c.update(
        mechanism=path,
        surface_phase="catalyst",
        catalyst_area_m2=0.0,
        retentate_void_volume_m3=0.0,
    )
    c["membrane"].update(species="A", area_m2=0.0)
    c["heat"].update(interchannel_UA_W_K=0.0, wall_UA_W_K=0.0)
    for side in ["retentate", "sweep"]:
        c[side].update(
            temperature_K=700.0,
            pressure_Pa=1e5,
            mole_fractions={"A": 0.4, "B": 0.1, "I": 0.5},
        )
    return c


def run(c):
    """Check input immutability, finite JSON, and requested profile ordering.

    Contract: instruction.md, "输出" and "调用、失败与资源".
    """
    before = copy.deepcopy(c)
    r = candidate.simulate_module(c)
    assert c == before
    json.dumps(r, allow_nan=False)
    assert r["flow_direction"] == c["flow_direction"]
    assert len(r["profiles"]) == len(c["sample_positions_m"])
    assert [p["z_m"] for p in r["profiles"]] == c["sample_positions_m"]
    return r


def finite_volume_samples(result, positions):
    """Interpolate independent cell-boundary solutions at the public grid."""
    columns = [
        np.column_stack(
            [
                np.interp(
                    positions, result["positions"], result[side + "_profile"][:, column]
                )
                for column in range(result[side + "_profile"].shape[1])
            ]
        )
        for side in ("retentate", "sweep")
    ]
    return np.stack(columns, axis=1)


@pytest.mark.parametrize("direction", ["co-current", "counter-current"])
def test_constant_cp_heat_exchanger_closed_form(analytic_mechanism, direction):
    """“顺流扫气入口也在 z=0，逆流扫气入口则在 z=L。”

    Constant-cp analytic energy balances independently fix both temperature
    profiles, retaining the published 0.0004 K limit at every requested point.
    """
    c = analytic_configuration(analytic_mechanism, direction)
    c["retentate"].update(temperature_K=800.0, mole_fractions={"I": 1.0})
    c["sweep"].update(temperature_K=500.0, mole_fractions={"I": 1.0})
    c["heat"]["interchannel_UA_W_K"] = 0.01
    r = run(c)
    position = np.asarray(c["sample_positions_m"]) / c["length_m"]
    co_difference = 300 * np.exp(-0.01 * (1 / 0.02 + 1 / 0.04) * position)
    co_r = (0.02 * 800 + 0.04 * 500 + 0.04 * co_difference) / 0.06
    a = 0.01 * (1 / 0.02 - 1 / 0.04)
    b = (1 / 0.02) / (1 / 0.02 - 1 / 0.04)
    d0 = 300 / (b * (1 - np.exp(-a)) + np.exp(-a))
    exponential = np.exp(-a * position)
    counter_r = 800 - b * d0 * (1 - exponential)
    expected_r, expected_s = {
        "co-current": (co_r, co_r - co_difference),
        "counter-current": (counter_r, counter_r - d0 * exponential),
    }[direction]
    actual = np.array(
        [
            [p[side]["temperature_K"] for side in ("retentate", "sweep")]
            for p in r["profiles"]
        ]
    )
    assert actual.shape == (len(position), 2)
    assert actual[:, 0] == pytest.approx(expected_r, abs=4e-4), "retentate closed form"
    assert actual[:, 1] == pytest.approx(expected_s, abs=4e-4), "sweep closed form"


@pytest.mark.parametrize("direction", ["co-current", "counter-current"])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize(
    "permuted", [False, True], ids=["original-species", "permuted-species"]
)
def test_isothermal_pure_species_membrane_exact_flux(
    analytic_mechanism, direction, reverse, permuted
):
    """“该关系允许负通量。膜面积沿长度均匀分布。”

    A pure species has constant partial pressure, so signed transfer is
    exactly linear; both inlet locations and both signs are registered cases.
    """
    mechanism = Path(analytic_mechanism)
    species_order = {False: "[A, B, I]", True: "[I, A, B]"}[permuted]
    mechanism.write_text(mechanism.read_text().replace("[A, B, I]", species_order))
    c = analytic_configuration(analytic_mechanism, direction)
    for side in ("retentate", "sweep"):
        c[side]["mole_fractions"] = {"A": 1.0}
    c["retentate"]["pressure_Pa"] = {False: 1.2e5, True: 8e4}[reverse]
    c["membrane"].update(area_m2=0.01, permeance_kmol_m2_s_Pa=1e-10)
    r = run(c)
    x = np.asarray(c["sample_positions_m"]) / c["length_m"]
    transfer = 0.01 * 1e-10 * (c["retentate"]["pressure_Pa"] - 1e5)
    progress = {"co-current": x, "counter-current": 1 - x}[direction]
    actual_r = np.array(
        [p["retentate"]["species_flow_kmol_s"]["A"] for p in r["profiles"]]
    )
    actual_s = np.array([p["sweep"]["species_flow_kmol_s"]["A"] for p in r["profiles"]])
    actual_j = np.array([p["membrane_transfer_kmol_s_m"] for p in r["profiles"]])
    actual_t = np.array(
        [
            [p[side]["temperature_K"] for side in ("retentate", "sweep")]
            for p in r["profiles"]
        ]
    )
    assert actual_t.shape == (len(x), 2)
    assert actual_r == pytest.approx(1e-6 - transfer * x, rel=5e-6)
    assert actual_s == pytest.approx(2e-6 + transfer * progress, rel=5e-6)
    assert actual_j == pytest.approx(
        np.full(len(x), transfer / c["length_m"]), rel=5e-6
    )
    assert actual_t == pytest.approx(np.full((len(x), 2), 700.0), abs=4e-4)


@pytest.mark.parametrize(
    "product_weight", [0.1, 0.0], ids=["present-product", "zero-inlet-product"]
)
def test_homogeneous_isomerization_analytic_decay(analytic_mechanism, product_weight):
    """“允许某些物种入口为零，不能据此禁止其通过反应生成或穿膜进入。”

    Equal-thermo first-order isomers give exponential A decay and an exact B
    gain. The zero-inlet-product case separately covers a legal domain edge.
    """
    c = analytic_configuration(analytic_mechanism)
    c["retentate_void_volume_m3"] = 2e-5
    c["retentate"]["mole_fractions"] = {
        "A": 0.4,
        "B": product_weight,
        "I": 0.6 - product_weight,
    }
    r = run(c)
    x = np.asarray(c["sample_positions_m"]) / c["length_m"]
    expected_a = 0.4e-6 * np.exp(
        -0.4 * 1e5 / (ct.gas_constant * 700) * c["retentate_void_volume_m3"] / 1e-6 * x
    )
    expected_b = product_weight * 1e-6 + 0.4e-6 - expected_a
    actual = np.array(
        [
            [p["retentate"]["species_flow_kmol_s"][name] for name in ("A", "B")]
            for p in r["profiles"]
        ]
    )
    assert actual.shape == (len(x), 2)
    assert actual[:, 0] == pytest.approx(expected_a, rel=5e-6)
    assert actual[:, 1] == pytest.approx(expected_b, rel=5e-6, abs=2e-11)


@pytest.mark.parametrize("direction", ["co-current", "counter-current"])
def test_membrane_enters_species_absent_from_receiver(analytic_mechanism, direction):
    """“允许某些物种入口为零，不能据此禁止其通过反应生成或穿膜进入。”

    A pure donor and an inert sweep at equal pressure give a scalar exact
    balance: accumulated transfer plus its square divided by twice the inert
    flow equals area times permeance times pressure times axial progress.
    Both inlet arrangements retain a strictly positive total flow.
    """
    c = analytic_configuration(analytic_mechanism, direction)
    c["retentate"]["mole_fractions"] = {"A": 1.0}
    c["sweep"]["mole_fractions"] = {"I": 1.0}
    c["membrane"].update(area_m2=0.01, permeance_kmol_m2_s_Pa=1e-10)
    result = run(c)
    x = np.asarray(c["sample_positions_m"]) / c["length_m"]
    inert = 2e-6
    driving = 0.01 * 1e-10 * 1e5
    total = 2 * driving * inert / (np.sqrt(inert**2 + 2 * driving * inert) + inert)
    co_transfer = (
        2 * driving * inert * x / (np.sqrt(inert**2 + 2 * driving * inert * x) + inert)
    )
    counter_transfer = (
        2
        * driving
        * inert
        * x
        / (inert + total + np.sqrt((inert + total) ** 2 - 2 * driving * inert * x))
    )
    removed, received = {
        "co-current": (co_transfer, co_transfer),
        "counter-current": (counter_transfer, total - counter_transfer),
    }[direction]
    flows = np.array(
        [
            [
                [p[side]["species_flow_kmol_s"][name] for name in ("A", "B", "I")]
                for side in ("retentate", "sweep")
            ]
            for p in result["profiles"]
        ]
    )
    temperatures = np.array(
        [
            [p[side]["temperature_K"] for side in ("retentate", "sweep")]
            for p in result["profiles"]
        ]
    )
    transfer = np.array([p["membrane_transfer_kmol_s_m"] for p in result["profiles"]])
    expected = np.zeros((len(x), 2, 3))
    expected[:, 0, 0] = 1e-6 - removed
    expected[:, 1, 0] = received
    expected[:, 1, 2] = inert
    assert flows.shape == expected.shape
    assert flows == pytest.approx(expected, rel=2e-5, abs=2e-11)
    assert temperatures.shape == (len(x), 2)
    assert temperatures == pytest.approx(np.full((len(x), 2), 700.0), abs=4e-4)
    assert transfer == pytest.approx(
        driving * inert / (inert + received) / c["length_m"], rel=5e-6, abs=1e-13
    )


def test_surface_kinetics_analytic_coverage_and_conversion(analytic_mechanism):
    """“表面吸附物在每个位置达到当地稳态，覆盖度非负且总和为 1”

    The three-site linear kinetic cycle has rational coverages and an
    integrated conversion relation, independent of any numerical reactor ODE.
    """
    c = analytic_configuration(analytic_mechanism)
    c["catalyst_area_m2"] = 0.08
    r = run(c)
    x = np.asarray(c["sample_positions_m"]) / c["length_m"]
    flow = np.array([p["retentate"]["species_flow_kmol_s"]["A"] for p in r["profiles"]])
    q = 200 * 1e5 / (ct.gas_constant * 700 * 1e-6)
    a = q * flow
    vacancy = 1 / (1 + a / 8 * (1 + 5 / 7))
    expected_coverage = np.column_stack(
        (vacancy, a * vacancy / 8, 5 * a * vacancy / 56)
    )
    actual = np.array(
        [
            [p["surface_coverages"][name] for name in ("S", "AS", "BS")]
            for p in r["profiles"]
        ]
    )
    expected_flow = np.array(
        [
            brentq(
                lambda f, rhs=rhs: 8 * np.log(4e-7 / f)
                + (12 / 7) * q * (4e-7 - f)
                - rhs,
                4e-8,
                4e-7,
                xtol=1e-18,
            )
            for rhs in c["catalyst_area_m2"] * 1e-6 * 5 * q * x
        ]
    )
    assert all(set(p["surface_coverages"]) == {"S", "AS", "BS"} for p in r["profiles"])
    assert actual.shape == (len(x), 3)
    assert actual == pytest.approx(expected_coverage, rel=3e-6, abs=1e-8)
    assert flow == pytest.approx(expected_flow, rel=5e-6)


@pytest.mark.parametrize("rate_scale", [1e-12, 1e-16], ids=["slow", "very-slow"])
def test_surface_steady_branch_invariant_to_kinetic_time_scale(
    analytic_mechanism, tmp_path, rate_scale
):
    """“按该机理的有限速率动力学充分松弛后的极限定义覆盖度。”

    A common positive multiplier changes the relaxation clock, not the
    attracting steady state. Analytic coverages expose false convergence from
    a fixed relaxation horizon or an absolute-rate stopping test alone.
    """
    import re

    original = Path(analytic_mechanism).read_text()
    header, surface = original.split("surface-reactions:\n")
    scaled = re.sub(
        r"A: ([0-9.]+)", lambda m: "A: " + str(float(m.group(1)) * rate_scale), surface
    )
    mechanism = tmp_path / "slow-surface.yaml"
    mechanism.write_text(header + "surface-reactions:\n" + scaled)
    c = analytic_configuration(str(mechanism), points=3)
    r = run(c)
    a = 200 * 1e5 * 0.4 / (ct.gas_constant * 700)
    vacancy = 1 / (1 + a / 8 * (1 + 5 / 7))
    expected = np.tile([vacancy, a * vacancy / 8, 5 * a * vacancy / 56], (3, 1))
    actual = np.array(
        [
            [p["surface_coverages"][name] for name in ("S", "AS", "BS")]
            for p in r["profiles"]
        ]
    )
    assert actual.shape == (3, 3)
    assert actual == pytest.approx(expected, rel=3e-6, abs=1e-8)


@pytest.mark.parametrize("direction", ["co-current", "counter-current"])
def test_real_mechanism_conservation_and_local_thermochemistry(direction):
    """“每侧焓流应与返回的物种流量和温度自洽”

    Reconstruct every sample using a trusted mechanism, independently relax
    the physical surface from its default, and compare full arrays directly.
    Element/enthalpy balances, boundaries and local fluxes remain separate.
    """
    c = configuration(direction, 161)
    r = run(c)
    gas = ct.Solution(MECH, "gas")
    sweep = ct.Solution(MECH, "gas")
    surface = ct.Interface(MECH, "Ru_surface", adjacent=[gas])
    relaxed = ct.Interface(MECH, "Ru_surface", adjacent=[gas])
    initial = relaxed.coverages.copy()
    signed = {"co-current": 1, "counter-current": -1}[direction]
    species_names = gas.species_names
    observed_flow, pressure, enthalpy, computed_h = [], [], [], []
    totals, total_h, wall, observed_c, expected_c = [], [], [], [], []
    (
        residual,
        relaxed_residual,
        relaxation_change,
        expected_j,
        expected_q,
        expected_w,
    ) = ([], [], [], [], [], [])
    for p in r["profiles"]:
        local_flow, local_h, elemental = [], [], []
        for side, phase in (("retentate", gas), ("sweep", sweep)):
            state = p[side]
            flow = np.array(
                [state["species_flow_kmol_s"][name] for name in species_names]
            )
            phase.TPX = state["temperature_K"], state["pressure_Pa"], flow
            h = sum(flow) * phase.enthalpy_mole
            local_flow.append(flow)
            local_h.append(h)
            elemental.append(
                np.array(
                    [
                        sum(
                            flow[i] * phase.n_atoms(i, e)
                            for i in range(phase.n_species)
                        )
                        for e in phase.element_names
                    ]
                )
            )
            pressure.append(state["pressure_Pa"] / c[side]["pressure_Pa"])
            enthalpy.append(state["enthalpy_flow_W"])
            computed_h.append(h)
        observed_flow.append(local_flow)
        totals.append(elemental[0] + signed * elemental[1])
        total_h.append(local_h[0] + signed * local_h[1])
        wall.append(p["wall_heat_W_m"])
        observed_c.append(
            [p["surface_coverages"][name] for name in surface.species_names]
        )
        surface.TP = gas.T, gas.P
        surface.coverages = p["surface_coverages"]
        residual.append(
            max(abs(surface.get_net_production_rates(surface))) / surface.site_density
        )
        relaxed.TP = gas.T, gas.P
        relaxed.coverages = initial
        relaxed.advance_coverages(1.0, rtol=1e-11, atol=1e-17)
        first = relaxed.coverages.copy()
        relaxed.advance_coverages(1.0, rtol=1e-11, atol=1e-17)
        expected_c.append(relaxed.coverages.copy())
        relaxation_change.append(max(abs(relaxed.coverages - first)))
        relaxed_residual.append(
            max(abs(relaxed.get_net_production_rates(relaxed))) / relaxed.site_density
        )
        k = gas.species_index("H2")
        expected_j.append(
            c["membrane"]["area_m2"]
            * c["membrane"]["permeance_kmol_m2_s_Pa"]
            / c["length_m"]
            * (gas.P * gas.X[k] - sweep.P * sweep.X[k])
        )
        expected_q.append(
            c["heat"]["interchannel_UA_W_K"] / c["length_m"] * (gas.T - sweep.T)
        )
        expected_w.append(
            c["heat"]["wall_UA_W_K"]
            / c["length_m"]
            * (c["heat"]["wall_temperature_K"] - gas.T)
        )
    flows, coverages, totals = (
        np.asarray(observed_flow),
        np.asarray(observed_c),
        np.asarray(totals),
    )
    assert flows.shape == (161, 2, gas.n_species)
    assert np.min(flows) >= -1e-14
    assert np.all(np.sum(flows, axis=-1) > 0)
    assert np.asarray(pressure) == pytest.approx(np.ones(322), rel=1e-9)
    assert np.asarray(enthalpy) == pytest.approx(
        np.asarray(computed_h), rel=3e-7, abs=1e-7
    )
    assert all(
        set(p[side]["species_flow_kmol_s"]) == set(species_names)
        for p in r["profiles"]
        for side in ("retentate", "sweep")
    )
    assert all(
        set(p["surface_coverages"]) == set(surface.species_names) for p in r["profiles"]
    )
    assert all(
        isinstance(v, (int, float))
        and not isinstance(v, bool)
        and np.isfinite(v)
        and v >= 0
        for p in r["profiles"]
        for v in p["surface_coverages"].values()
    )
    assert np.max(abs(np.sum(coverages, axis=-1) - 1)) < 1e-8
    assert max(residual) < 2e-6, "candidate coverage kinetic residual"
    assert max(relaxation_change) < 2e-10, "independent physical relaxation convergence"
    assert max(relaxed_residual) < 2e-7
    assert coverages == pytest.approx(np.asarray(expected_c), rel=3e-6, abs=1e-8)
    assert np.asarray(
        [p["membrane_transfer_kmol_s_m"] for p in r["profiles"]]
    ) == pytest.approx(np.asarray(expected_j), rel=5e-6, abs=1e-13)
    assert np.asarray(
        [p["interchannel_heat_W_m"] for p in r["profiles"]]
    ) == pytest.approx(np.asarray(expected_q), rel=5e-6, abs=1e-8)
    assert np.asarray(wall) == pytest.approx(np.asarray(expected_w), rel=5e-6, abs=1e-8)
    assert totals == pytest.approx(np.tile(totals[0], (161, 1)), rel=2e-6, abs=2e-12)
    assert total_h[-1] - total_h[0] == pytest.approx(
        np.trapezoid(wall, c["sample_positions_m"]), rel=3e-5, abs=2e-6
    )
    inlet_rows = [
        r["profiles"][0]["retentate"],
        r["profiles"][{"co-current": 0, "counter-current": -1}[direction]]["sweep"],
    ]
    inlet_expected = []
    for label in ("retentate", "sweep"):
        feed = c[label]
        gas.TPX = feed["temperature_K"], feed["pressure_Pa"], feed["mole_fractions"]
        inlet_expected.append(gas.X * feed["molar_flow_kmol_s"])
    assert np.array([p["temperature_K"] for p in inlet_rows]) == pytest.approx(
        [700.0, 650.0], abs=4e-4
    )
    assert np.array(
        [[p["species_flow_kmol_s"][name] for name in species_names] for p in inlet_rows]
    ) == pytest.approx(np.asarray(inlet_expected), rel=2e-6, abs=1e-13)


@pytest.mark.parametrize("direction", ["co-current", "counter-current"])
def test_independent_finite_volume_grid_convergence(direction):
    """“温度需要达到 0.002 K 绝对精度”

    Independent midpoint finite volumes check both outlets. Three meshes must
    converge before their independently bounded truncation error is used.
    """
    c = configuration(direction, 5)
    r = run(c)
    names = ct.Solution(MECH, "gas").species_names
    actual = np.array(
        [
            [
                [p[side]["species_flow_kmol_s"][x] for x in names]
                + [p[side]["temperature_K"]]
                for side in ("retentate", "sweep")
            ]
            for p in r["profiles"]
        ]
    )
    meshes = [solve_cells(c, cells) for cells in (12, 24, 48)]
    coarse, medium, fine = [
        finite_volume_samples(m, c["sample_positions_m"]) for m in meshes
    ]
    first, second = abs(medium - coarse), abs(fine - medium)
    business = 2e-5 * abs(fine[..., :-1]) + 2e-11
    flow_limit = business + second[..., :-1] / 2 + 1e-16
    assert actual.shape == fine.shape == (5, 2, len(names) + 1)
    assert np.all(
        second[..., :-1] <= 0.4 * first[..., :-1] + 1e-16
    ), "FV species mesh convergence"
    assert np.all(
        second[..., :-1] <= 0.25 * business
    ), "FV uncertainty exceeds species accuracy budget"
    assert np.all(
        second[..., -1] < 0.4 * first[..., -1] + 1e-7
    ), "FV temperature mesh convergence"
    assert np.all(second[..., -1] < 0.002)
    assert np.all(abs(actual[..., :-1] - fine[..., :-1]) <= flow_limit), (
        actual,
        fine,
        flow_limit,
    )
    assert np.all(
        abs(actual[..., -1] - fine[..., -1]) <= 0.002 + second[..., -1] / 2
    ), (actual[..., -1], fine[..., -1])


def test_inlet_weight_scaling_and_repeated_call_independence():
    """“连续调用及改变物种字典顺序不能污染结果”

    Compare all requested rows after an unrelated boundary-value solve and
    equivalent rescaling/reordering of both inlet weight dictionaries.
    """
    c = configuration(points=5)
    a = run(c)
    other = configuration("counter-current", 5)
    other["retentate"]["temperature_K"] = 710.0
    run(other)
    changed = copy.deepcopy(c)
    for side in ("retentate", "sweep"):
        changed[side]["mole_fractions"] = {
            k: v * 7.3 for k, v in reversed(list(c[side]["mole_fractions"].items()))
        }
    b = run(changed)
    names = ct.Solution(MECH, "gas").species_names
    temperatures = [
        np.array(
            [
                [p[s]["temperature_K"] for s in ("retentate", "sweep")]
                for p in result["profiles"]
            ]
        )
        for result in (a, b)
    ]
    flows = [
        np.array(
            [
                [
                    [p[s]["species_flow_kmol_s"][name] for name in names]
                    for s in ("retentate", "sweep")
                ]
                for p in result["profiles"]
            ]
        )
        for result in (a, b)
    ]
    assert temperatures[0].shape == temperatures[1].shape == (5, 2)
    assert temperatures[0] == pytest.approx(temperatures[1], abs=4e-4)
    assert flows[0].shape == flows[1].shape == (5, 2, len(names))
    assert flows[0] == pytest.approx(flows[1], rel=3e-6, abs=1e-13)


def test_hot_reverse_permeation_uses_donor_enthalpy():
    """“j>0 用反应侧，j<0 用扫气侧。”

    Hot hydrogen-bearing sweep transfers energy in reverse; converged,
    independent cell balances detect choosing the receiver enthalpy instead.
    """
    c = configuration("counter-current", 9)
    c["sweep"].update(
        temperature_K=780.0, pressure_Pa=4e5, mole_fractions={"H2": 0.2, "AR": 0.8}
    )
    c["membrane"]["permeance_kmol_m2_s_Pa"] = 1e-10
    c["catalyst_area_m2"] = 20.0
    r = run(c)
    names = ct.Solution(MECH, "gas").species_names
    actual = np.array(
        [
            [
                [p[side]["species_flow_kmol_s"][name] for name in names]
                + [p[side]["temperature_K"]]
                for side in ("retentate", "sweep")
            ]
            for p in r["profiles"]
        ]
    )
    meshes = [solve_cells(c, cells) for cells in (16, 32, 64)]
    coarse, medium, fine = [
        finite_volume_samples(m, c["sample_positions_m"]) for m in meshes
    ]
    first, second = abs(medium - coarse), abs(fine - medium)
    business = 2e-5 * abs(fine[..., :-1]) + 2e-11
    flow_limit = business + second[..., :-1] / 2 + 1e-16
    assert len(r["profiles"]) == 9
    assert all(p["membrane_transfer_kmol_s_m"] < 0 for p in r["profiles"])
    assert actual.shape == fine.shape == (9, 2, len(names) + 1)
    assert np.all(second[..., :-1] <= 0.4 * first[..., :-1] + 1e-16)
    assert np.all(second[..., :-1] <= 0.25 * business)
    assert np.all(abs(actual[..., :-1] - fine[..., :-1]) <= flow_limit), (
        actual,
        fine,
        flow_limit,
    )
    assert np.all(second[..., -1] < 0.4 * first[..., -1] + 1e-7)
    assert np.all(second[..., -1] < 0.003)
    assert np.all(
        abs(actual[..., -1] - fine[..., -1]) <= 0.002 + second[..., -1] / 2
    ), (actual[..., -1], fine[..., -1])


def test_membrane_donor_changes_with_axial_flux_direction():
    """“j>0 用反应侧，j<0 用扫气侧。”

    Reaction-generated hydrogen changes the sign of the partial-pressure
    difference along the channel. Independent finite-volume profiles use
    local donor enthalpy on each side of that crossing, with the same public
    error limits and three-grid uncertainty check as the fixed-sign case.
    """
    c = configuration("co-current", 9)
    c["sweep"].update(
        temperature_K=780.0, pressure_Pa=4e5, mole_fractions={"H2": 0.04, "AR": 0.96}
    )
    c["membrane"]["permeance_kmol_m2_s_Pa"] = 2e-11
    result = run(c)
    names = ct.Solution(MECH, "gas").species_names
    actual = np.array(
        [
            [
                [p[side]["species_flow_kmol_s"][name] for name in names]
                + [p[side]["temperature_K"]]
                for side in ("retentate", "sweep")
            ]
            for p in result["profiles"]
        ]
    )
    meshes = [solve_cells(c, cells) for cells in (16, 32, 64)]
    coarse, medium, fine = [
        finite_volume_samples(m, c["sample_positions_m"]) for m in meshes
    ]
    first, second = abs(medium - coarse), abs(fine - medium)
    business = 2e-5 * abs(fine[..., :-1]) + 2e-11
    k = names.index("H2")
    independent_drive = 3e5 * fine[:, 0, k] / fine[:, 0, :-1].sum(axis=1) - 4e5 * fine[
        :, 1, k
    ] / fine[:, 1, :-1].sum(axis=1)
    assert independent_drive[0] < 0 < independent_drive[-1]
    assert result["profiles"][0]["membrane_transfer_kmol_s_m"] < 0
    assert result["profiles"][-1]["membrane_transfer_kmol_s_m"] > 0
    assert actual.shape == fine.shape == (9, 2, len(names) + 1)
    assert np.all(second[..., :-1] <= 0.4 * first[..., :-1] + 1e-16)
    assert np.all(second[..., :-1] <= 0.25 * business)
    assert np.all(
        abs(actual[..., :-1] - fine[..., :-1])
        <= business + second[..., :-1] / 2 + 1e-16
    )
    assert np.all(second[..., -1] < 0.4 * first[..., -1] + 1e-7)
    assert np.all(second[..., -1] < 0.003)
    assert np.all(abs(actual[..., -1] - fine[..., -1]) <= 0.002 + second[..., -1] / 2)


def test_default_mechanism_and_json_cli_are_consistent():
    """“命令行入口从标准输入读取 config 并输出结果 JSON。”

    Omitted defaults and the public stdin entry must reproduce explicit
    defaults. Compare temperatures, species and enthalpy as complete arrays.
    """
    explicit = configuration("counter-current", 5)
    trusted = run(explicit)
    default = copy.deepcopy(explicit)
    for key in ("mechanism", "gas_phase", "surface_phase"):
        default.pop(key)
    implicit = run(default)
    command = run_cli(json.dumps(default), timeout=60)
    assert command.returncode == 0, command.stderr
    cli = json.loads(command.stdout)
    names = ct.Solution(MECH, "gas").species_names
    results = (trusted, implicit, cli)
    positions = [[p["z_m"] for p in result["profiles"]] for result in results]
    temperatures = [
        np.array(
            [
                [p[s]["temperature_K"] for s in ("retentate", "sweep")]
                for p in result["profiles"]
            ]
        )
        for result in results
    ]
    flows = [
        np.array(
            [
                [
                    [p[s]["species_flow_kmol_s"][name] for name in names]
                    for s in ("retentate", "sweep")
                ]
                for p in result["profiles"]
            ]
        )
        for result in results
    ]
    enthalpies = [
        np.array(
            [
                [p[s]["enthalpy_flow_W"] for s in ("retentate", "sweep")]
                for p in result["profiles"]
            ]
        )
        for result in results
    ]
    assert cli["flow_direction"] == default["flow_direction"]
    assert positions == [c for c in [default["sample_positions_m"]] * 3]
    assert temperatures[1] == pytest.approx(temperatures[0], abs=4e-4)
    assert temperatures[2] == pytest.approx(temperatures[0], abs=4e-4)
    assert flows[1] == pytest.approx(flows[0], rel=3e-6, abs=1e-13)
    assert flows[2] == pytest.approx(flows[0], rel=3e-6, abs=1e-13)
    assert enthalpies[1] == pytest.approx(enthalpies[0], rel=3e-7, abs=1e-7)
    assert enthalpies[2] == pytest.approx(enthalpies[0], rel=3e-7, abs=1e-7)


@pytest.mark.parametrize(
    "path,value",
    [
        pytest.param(("sweep", "molar_flow_kmol_s"), -1, id="negative-flow"),
        pytest.param(
            ("retentate", "mole_fractions", "not-species"), 0, id="unknown-species"
        ),
        pytest.param(("flow_direction",), "parallel", id="bad-direction"),
        pytest.param(("sample_positions_m",), [0, 0.05, 0.04], id="bad-grid"),
        pytest.param(("sweep", "pressure_Pa"), True, id="bool-pressure"),
        pytest.param(
            ("retentate", "temperature_K"), float("nan"), id="nan-temperature"
        ),
        pytest.param(("membrane", "area_m2"), float("inf"), id="infinite-area"),
        pytest.param(
            ("retentate", "mole_fractions"), {"NH3": -1.0}, id="negative-weight"
        ),
        pytest.param(
            ("retentate", "mole_fractions"), {"NH3": 0.0}, id="zero-total-weight"
        ),
        pytest.param(("membrane", "species"), "unknown", id="unknown-membrane-species"),
        pytest.param(
            ("mechanism",),
            "missing-unloadable-mechanism.yaml",
            id="unloadable-mechanism",
        ),
        pytest.param(("gas_phase",), "missing-gas-phase", id="unknown-gas-phase"),
        pytest.param(
            ("surface_phase",), "missing-surface-phase", id="unknown-surface-phase"
        ),
        pytest.param(("sample_positions_m",), [0.0], id="too-few-samples"),
        pytest.param(
            ("sample_positions_m",), [0.0, 0.049], id="wrong-final-coordinate"
        ),
        pytest.param(
            ("sample_positions_m",),
            [0.0, 0.025, 0.025, 0.05],
            id="duplicate-interior-coordinate",
        ),
        pytest.param(
            ("sample_positions_m",),
            [0.0, 0.03, 0.02, 0.05],
            id="reversed-interior-coordinates",
        ),
        pytest.param(
            ("sample_positions_m",),
            np.linspace(0.0, 0.05, 202).tolist(),
            id="too-many-samples",
        ),
    ],
)
def test_invalid_configuration_is_rejected(path, value):
    """“缺字段、非法数值、未知物种、不能加载的机理/相”

    Each original rejected-input case is registered explicitly. All execute
    the same ValueError assertion; no branch can silently omit the check.
    The contract specifies the exception class, not the private message text.
    """
    c = configuration(points=3)
    parent = c
    for field in path[:-1]:
        parent = parent[field]
    parent[path[-1]] = value
    with pytest.raises(ValueError):  # noqa: PT011
        candidate.simulate_module(c)


def test_non_dictionary_configuration_is_rejected():
    """“`config` 是字典，额外字段可以忽略。”

    An empty list must raise the specified public input error rather than
    leaking an attribute error or accepting a different input structure.
    """
    with pytest.raises(ValueError):  # noqa: PT011
        candidate.simulate_module([])


@pytest.mark.parametrize(
    "missing",
    [
        "length_m",
        "catalyst_area_m2",
        "retentate_void_volume_m3",
        "flow_direction",
        "sample_positions_m",
        "retentate",
        "sweep",
        "membrane",
        "heat",
    ],
)
def test_missing_required_field_rejected_before_clean_recovery(
    analytic_mechanism, missing
):
    """“缺字段、非法数值、未知物种、不能加载的机理/相”

    All required top-level fields are exercised. A following cheap, valid
    analytic call in the same worker must retain the specified result.
    """
    valid = analytic_configuration(analytic_mechanism, points=2)
    broken = copy.deepcopy(valid)
    del broken[missing]
    with pytest.raises(ValueError):  # noqa: PT011
        candidate.simulate_module(broken)
    recovered = run(valid)
    assert len(recovered["profiles"]) == 2
    assert np.array(
        [p["retentate"]["temperature_K"] for p in recovered["profiles"]]
    ) == pytest.approx([700.0, 700.0], abs=4e-4)
    assert np.array(
        [p["retentate"]["species_flow_kmol_s"]["A"] for p in recovered["profiles"]]
    ) == pytest.approx([4e-7, 4e-7], rel=5e-6)


@pytest.mark.parametrize("count", [2, 201])
def test_output_schema_and_valid_tuple_coordinate_boundaries(analytic_mechanism, count):
    """“返回值由普通 Python 数据结构和有限数值组成，支持 JSON”

    Exercise both legal sample-count edges with tuple input. Check each
    declared structure, complete species sets, finite non-boolean numbers,
    and exact requested coordinates before comparing their physical values.
    No dictionary key order or JSON byte layout is imposed.
    """
    c = analytic_configuration(analytic_mechanism, points=count)
    c["sample_positions_m"] = tuple(c["sample_positions_m"])
    result = candidate.simulate_module(c)
    assert type(result) is dict
    assert set(result) == {"flow_direction", "profiles"}
    assert type(result["flow_direction"]) is str
    assert result["flow_direction"] == c["flow_direction"]
    assert type(result["profiles"]) is list
    assert len(result["profiles"]) == count
    points = result["profiles"]
    point_fields = {
        "z_m",
        "retentate",
        "sweep",
        "surface_coverages",
        "membrane_transfer_kmol_s_m",
        "interchannel_heat_W_m",
        "wall_heat_W_m",
    }
    side_fields = {
        "temperature_K",
        "pressure_Pa",
        "species_flow_kmol_s",
        "enthalpy_flow_W",
    }
    assert all(type(p) is dict and set(p) == point_fields for p in points)
    assert all(
        type(p[s]) is dict and set(p[s]) == side_fields
        for p in points
        for s in ("retentate", "sweep")
    )
    assert all(
        type(p[s]["species_flow_kmol_s"]) is dict
        and set(p[s]["species_flow_kmol_s"]) == {"A", "B", "I"}
        for p in points
        for s in ("retentate", "sweep")
    )
    assert all(
        type(p["surface_coverages"]) is dict
        and set(p["surface_coverages"]) == {"S", "AS", "BS"}
        for p in points
    )
    numbers = [
        p[k]
        for p in points
        for k in (
            "z_m",
            "membrane_transfer_kmol_s_m",
            "interchannel_heat_W_m",
            "wall_heat_W_m",
        )
    ]
    numbers += [
        p[s][k]
        for p in points
        for s in ("retentate", "sweep")
        for k in ("temperature_K", "pressure_Pa", "enthalpy_flow_W")
    ]
    numbers += [
        v
        for p in points
        for s in ("retentate", "sweep")
        for v in p[s]["species_flow_kmol_s"].values()
    ]
    numbers += [v for p in points for v in p["surface_coverages"].values()]
    assert all(type(v) in (int, float) and np.isfinite(v) for v in numbers)
    assert tuple(p["z_m"] for p in points) == c["sample_positions_m"]
    encoded = json.dumps(result, allow_nan=False)
    assert json.loads(encoded) == result


@pytest.mark.parametrize(
    "slow_rate",
    [1e-8, 1e-20],
    ids=["eight-decade-separation", "twenty-decade-separation"],
)
def test_surface_weak_link_reaches_unique_attracting_distribution(
    analytic_mechanism, tmp_path, slow_rate
):
    """“在机理守恒约束确定的可达状态空间内局部吸引”

    Four reversible, equal-thermo surface states have exact uniform limiting
    coverage. A weak link separates two fast pairs but cannot be discarded
    based on the aggregate fast-reaction turnover or a fixed time horizon.
    This is a rate-scale correctness boundary, not the main industrial task.
    """
    header = """units: {length: m, quantity: kmol, time: s, activation-energy: J/kmol}
phases:
- name: gas
  thermo: ideal-gas
  kinetics: gas
  elements: [Ar]
  species: [I]
  reactions: none
- name: catalyst
  thermo: ideal-surface
  adjacent-phases: [gas]
  elements: [Pt]
  species: [S, AS, BS, CS]
  kinetics: surface
  reactions: [surface-reactions]
  site-density: 1e-6
species:
- name: I
  composition: {Ar: 1}
  thermo: {model: constant-cp, T0: 300, h0: 0, s0: 0, cp0: 20000}
"""
    states = "".join(
        "- name: " + name + "\n  composition: {Pt: 1}\n  thermo: {model: constant-cp}\n"
        for name in ("S", "AS", "BS", "CS")
    )
    reactions = "surface-reactions:\n" + "".join(
        "- equation: "
        + left
        + " <=> "
        + right
        + "\n  rate-constant: {A: "
        + str(rate)
        + ", b: 0, Ea: 0}\n"
        for left, right, rate in (
            ("S", "AS", 1.0),
            ("AS", "BS", slow_rate),
            ("BS", "CS", 1.0),
        )
    )
    path = tmp_path / "four-state.yaml"
    path.write_text(header + states + reactions)
    c = analytic_configuration(str(path), points=2)
    for side in ("retentate", "sweep"):
        c[side]["mole_fractions"] = {"I": 1.0}
    c["membrane"]["species"] = "I"
    result = run(c)
    actual = np.array(
        [
            [p["surface_coverages"][name] for name in ("S", "AS", "BS", "CS")]
            for p in result["profiles"]
        ]
    )
    assert actual.shape == (2, 4)
    assert actual == pytest.approx(np.full((2, 4), 0.25), rel=3e-6, abs=1e-8)


@pytest.mark.parametrize("phase", ["gas", "surface"])
def test_excess_species_mechanism_rejected_without_altering_input(
    analytic_mechanism, tmp_path, phase
):
    """“气相最多 8 个物种、20 个反应，表面最多 8 个物种、30 个反应。”

    Add six chemically inert species to one three-species phase. The YAML
    remains loadable; rejection therefore checks the declared size boundary.
    """
    original = Path(analytic_mechanism).read_text()
    new_names = ["EXTRA" + str(i) for i in range(6)]
    old_species, composition = {
        "gas": ("A, B, I", "Ar"),
        "surface": ("S, AS, BS", "Pt"),
    }[phase]
    changed = original.replace(
        "species: [" + old_species + "]",
        "species: [" + old_species + ", " + ", ".join(new_names) + "]",
    )
    inserted = "".join(
        "- name: "
        + name
        + "\n  composition: {"
        + composition
        + ": 1}\n  thermo: {model: constant-cp}\n"
        for name in new_names
    )
    changed = changed.replace("gas-reactions:\n", inserted + "gas-reactions:\n")
    path = tmp_path / "oversize.yaml"
    path.write_text(changed)
    gas = ct.Solution(str(path), "gas")
    surface = ct.Interface(str(path), "catalyst", adjacent=[gas])
    assert {"gas": gas.n_species, "surface": surface.n_species}[phase] == 9
    config = analytic_configuration(str(path), points=2)
    with pytest.raises(ValueError):  # noqa: PT011
        candidate.simulate_module(config)

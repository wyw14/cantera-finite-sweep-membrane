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


def species_mesh_budget(coarse, medium, fine):
    """Observed second-order error allowance, separate from interface accuracy."""
    first = np.abs(np.asarray(medium) - np.asarray(coarse))
    second = np.abs(np.asarray(fine) - np.asarray(medium))
    # 1e-16 kmol/s resolves cancellation in unchanged inert/zero components;
    # it is 2e5 times smaller than the stated near-zero species tolerance.
    floor = 1e-16
    assert np.all(second <= 0.4 * first + floor)
    business = 2e-5 * np.abs(fine) + 2e-11
    # An under-resolved reference is not allowed to inflate the acceptance band.
    assert np.all(second <= 0.25 * business)
    return second / 2 + floor


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


@pytest.mark.parametrize("direction", ["co-current", "counter-current"])
def test_constant_cp_heat_exchanger_closed_form(analytic_mechanism, direction):
    """Match constant-heat-capacity heat exchange for both inlet arrangements.

    Contract: "模型与工况" defines interchannel heat transfer and the sweep
    inlet at z=0 or z=L; "输出" sets 0.0004 K accuracy in this limit.
    """
    c = analytic_configuration(analytic_mechanism, direction)
    c["retentate"].update(temperature_K=800.0, mole_fractions={"I": 1.0})
    c["sweep"].update(temperature_K=500.0, mole_fractions={"I": 1.0})
    c["heat"]["interchannel_UA_W_K"] = 0.01
    r = run(c)
    C1 = 0.02
    C2 = 0.04
    U = 0.01
    if direction == "co-current":
        for p in r["profiles"]:
            d = 300 * np.exp(-U * (1 / C1 + 1 / C2) * p["z_m"] / c["length_m"])
            tr = (C1 * 800 + C2 * 500 + C2 * d) / (C1 + C2)
            ts = tr - d
            assert p["retentate"]["temperature_K"] == pytest.approx(tr, abs=4e-4)
            assert p["sweep"]["temperature_K"] == pytest.approx(ts, abs=4e-4)
    else:
        a = U * (1 / C1 - 1 / C2)
        b = (1 / C1) / (1 / C1 - 1 / C2)
        d0 = 300 / (b * (1 - np.exp(-a)) + np.exp(-a))
        for p in r["profiles"]:
            e = np.exp(-a * p["z_m"] / c["length_m"])
            tr = 800 - b * d0 * (1 - e)
            ts = tr - d0 * e
            assert p["retentate"]["temperature_K"] == pytest.approx(tr, abs=4e-4)
            assert p["sweep"]["temperature_K"] == pytest.approx(ts, abs=4e-4)


@pytest.mark.parametrize("direction", ["co-current", "counter-current"])
@pytest.mark.parametrize("reverse", [False, True])
def test_isothermal_pure_species_membrane_exact_flux(
    analytic_mechanism, direction, reverse
):
    """Match signed pure-species permeation and each side's inlet boundary.

    Contract: "模型与工况" allows either flux direction and defines both flow
    arrangements; "输出" specifies per-length transfer and 5e-6 accuracy.
    """
    c = analytic_configuration(analytic_mechanism, direction)
    for side in ["retentate", "sweep"]:
        c[side]["mole_fractions"] = {"A": 1.0}
    c["retentate"]["pressure_Pa"] = 8e4 if reverse else 1.2e5
    c["membrane"].update(area_m2=0.01, permeance_kmol_m2_s_Pa=1e-10)
    r = run(c)
    transfer = 0.01 * 1e-10 * (c["retentate"]["pressure_Pa"] - 1e5)
    for p in r["profiles"]:
        x = p["z_m"] / c["length_m"]
        assert p["retentate"]["species_flow_kmol_s"]["A"] == pytest.approx(
            1e-6 - transfer * x, rel=5e-6
        )
        progress = x if direction == "co-current" else 1 - x
        assert p["sweep"]["species_flow_kmol_s"]["A"] == pytest.approx(
            2e-6 + transfer * progress, rel=5e-6
        )
        assert p["membrane_transfer_kmol_s_m"] == pytest.approx(
            transfer / c["length_m"], rel=5e-6
        )
        assert p["retentate"]["temperature_K"] == pytest.approx(700, abs=4e-4)


def test_homogeneous_isomerization_analytic_decay(analytic_mechanism):
    """Match finite-rate gas conversion with the specified reacting volume.

    Contract: "模型与工况" scales gas production by the effective volume;
    "输出" requires 5e-6 relative accuracy for the gas-reaction limit.
    """
    c = analytic_configuration(analytic_mechanism)
    c["retentate_void_volume_m3"] = 2e-5
    r = run(c)
    F0 = 1e-6
    Cin = 1e5 / (ct.gas_constant * 700)
    for p in r["profiles"]:
        expected = (
            0.4
            * F0
            * np.exp(
                -0.4
                * Cin
                * c["retentate_void_volume_m3"]
                / F0
                * p["z_m"]
                / c["length_m"]
            )
        )
        assert p["retentate"]["species_flow_kmol_s"]["A"] == pytest.approx(
            expected, rel=5e-6
        )


def test_surface_kinetics_analytic_coverage_and_conversion(analytic_mechanism):
    """Match steady coverages and conversion for a soluble surface mechanism.

    Contract: "模型与工况" requires local kinetic surface steady states and
    catalytic-area scaling; "输出" gives coverage and surface-limit accuracy.
    """
    c = analytic_configuration(analytic_mechanism)
    c["catalyst_area_m2"] = 0.08
    r = run(c)
    q = 200 * 1e5 / (ct.gas_constant * 700 * 1e-6)
    Fin = 4e-7
    for p in r["profiles"]:
        F = p["retentate"]["species_flow_kmol_s"]["A"]
        a = q * F
        ts = 1 / (1 + a / 8 * (1 + 5 / 7))
        ta = a * ts / 8
        tb = 5 * ta / 7
        assert set(p["surface_coverages"]) == {"S", "AS", "BS"}
        np.testing.assert_allclose(
            [p["surface_coverages"][x] for x in ["S", "AS", "BS"]],
            [ts, ta, tb],
            rtol=3e-6,
            atol=1e-8,
        )
        rhs = c["catalyst_area_m2"] * 1e-6 * 5 * q * p["z_m"] / c["length_m"]
        expected = (
            Fin
            if rhs == 0
            else brentq(
                # brentq consumes this callback before the next loop iteration.
                lambda f, rhs=rhs: 8 * np.log(Fin / f) + (12 / 7) * q * (Fin - f) - rhs,
                Fin / 10,
                Fin,
                xtol=1e-18,
            )
        )
        assert F == pytest.approx(expected, rel=5e-6)


@pytest.mark.parametrize("direction", ["co-current", "counter-current"])
def test_real_mechanism_conservation_and_local_thermochemistry(direction):
    """Check both flow arrangements against the supplied mechanism's physics.

    Contract: "模型与工况" defines the attracting surface branch, signed
    exchanges and inlet locations; "输出" gives element/enthalpy conservation,
    coverage, pressure, thermochemical consistency and local-flux tolerances.
    """
    c = configuration(direction, 161)
    r = run(c)
    gas = ct.Solution(MECH, "gas")
    sw = ct.Solution(MECH, "gas")
    surf = ct.Interface(MECH, "Ru_surface", adjacent=[gas])
    relaxed = ct.Interface(MECH, "Ru_surface", adjacent=[gas])
    default_coverage = relaxed.coverages.copy()
    signed = 1 if direction == "co-current" else -1
    totals = []
    enthalpies = []
    wall = []
    for p in r["profiles"]:
        flows = []
        hs = []
        for label, g in [("retentate", gas), ("sweep", sw)]:
            state = p[label]
            F = np.array([state["species_flow_kmol_s"][s] for s in g.species_names])
            assert min(F) >= -1e-14
            assert sum(F) > 0
            assert state["pressure_Pa"] == pytest.approx(
                c[label]["pressure_Pa"], rel=1e-9
            )
            g.TPX = state["temperature_K"], state["pressure_Pa"], F
            H = sum(F) * g.enthalpy_mole
            assert state["enthalpy_flow_W"] == pytest.approx(H, rel=3e-7, abs=1e-7)
            flows.append(
                np.array(
                    [
                        sum(F[i] * g.n_atoms(i, e) for i in range(g.n_species))
                        for e in g.element_names
                    ]
                )
            )
            hs.append(H)
        totals.append(flows[0] + signed * flows[1])
        enthalpies.append(hs[0] + signed * hs[1])
        wall.append(p["wall_heat_W_m"])
        raw_coverages = p["surface_coverages"]
        assert isinstance(raw_coverages, dict)
        assert set(raw_coverages) == set(surf.species_names)
        assert all(
            isinstance(v, (int, float))
            and not isinstance(v, bool)
            and np.isfinite(v)
            and v >= 0
            for v in raw_coverages.values()
        )
        assert abs(sum(raw_coverages.values()) - 1) < 1e-8
        surf.TP = gas.T, gas.P
        surf.coverages = raw_coverages
        assert max(abs(surf.get_net_production_rates(surf))) / surf.site_density < 2e-6
        # Independent physical-time branch check: never initialize this surface
        # from candidate coverage, or an exactly blocked boundary could persist.
        relaxed.TP = gas.T, gas.P
        relaxed.coverages = default_coverage
        relaxed.advance_coverages(1.0, rtol=1e-11, atol=1e-17)
        first = relaxed.coverages.copy()
        relaxed.advance_coverages(1.0, rtol=1e-11, atol=1e-17)
        expected_coverage = relaxed.coverages.copy()
        assert np.max(abs(expected_coverage - first)) < 2e-10
        assert (
            max(abs(relaxed.get_net_production_rates(relaxed))) / relaxed.site_density
            < 2e-7
        )
        np.testing.assert_allclose(
            [raw_coverages[name] for name in relaxed.species_names],
            expected_coverage,
            rtol=3e-6,
            atol=1e-8,
        )
        k = gas.species_index("H2")
        J = (
            c["membrane"]["area_m2"]
            * c["membrane"]["permeance_kmol_m2_s_Pa"]
            / c["length_m"]
            * (gas.P * gas.X[k] - sw.P * sw.X[k])
        )
        assert p["membrane_transfer_kmol_s_m"] == pytest.approx(J, rel=5e-6, abs=1e-13)
        assert p["interchannel_heat_W_m"] == pytest.approx(
            c["heat"]["interchannel_UA_W_K"] / c["length_m"] * (gas.T - sw.T),
            rel=5e-6,
            abs=1e-8,
        )
        assert p["wall_heat_W_m"] == pytest.approx(
            c["heat"]["wall_UA_W_K"]
            / c["length_m"]
            * (c["heat"]["wall_temperature_K"] - gas.T),
            rel=5e-6,
            abs=1e-8,
        )
    np.testing.assert_allclose(
        totals, np.tile(totals[0], (len(totals), 1)), rtol=2e-6, atol=2e-12
    )
    assert enthalpies[-1] - enthalpies[0] == pytest.approx(
        np.trapezoid(wall, c["sample_positions_m"]), rel=3e-5, abs=2e-6
    )
    for label, index in [
        ("retentate", 0),
        ("sweep", 0 if direction == "co-current" else -1),
    ]:
        g = ct.Solution(MECH, "gas")
        f = c[label]
        g.TPX = f["temperature_K"], f["pressure_Pa"], f["mole_fractions"]
        p = r["profiles"][index][label]
        assert p["temperature_K"] == pytest.approx(f["temperature_K"], abs=4e-4)
        np.testing.assert_allclose(
            [p["species_flow_kmol_s"][x] for x in g.species_names],
            g.X * f["molar_flow_kmol_s"],
            rtol=2e-6,
            atol=1e-13,
        )


@pytest.mark.parametrize("direction", ["co-current", "counter-current"])
def test_independent_finite_volume_grid_convergence(direction):
    """Check coupled outlet states against a separately converged calculation.

    Contract: "模型与工况" defines the coupled steady model for both flow
    arrangements; "输出" sets 0.002 K and 2e-5/2e-11 kmol/s accuracy.
    The reference mesh is checked before it supplies an acceptance allowance.
    """
    c = configuration(direction, 5)
    r = run(c)
    coarse = solve_cells(c, 12)
    medium = solve_cells(c, 24)
    fine = solve_cells(c, 48)
    for label, index in [
        ("retentate_outlet", -1),
        ("sweep_outlet", -1 if direction == "co-current" else 0),
    ]:
        side = "retentate" if label.startswith("retentate") else "sweep"
        state = r["profiles"][index][side]
        names = ct.Solution(MECH, "gas").species_names
        actual = np.r_[
            [state["species_flow_kmol_s"][x] for x in names], state["temperature_K"]
        ]
        # Independent mesh convergence and agreement, not a stored solver trace.
        first = abs(medium[label][-1] - coarse[label][-1])
        second = abs(fine[label][-1] - medium[label][-1])
        assert second < 0.4 * first + 1e-7
        assert second < 0.002
        mesh_allowance = species_mesh_budget(
            coarse[label][:-1], medium[label][:-1], fine[label][:-1]
        )
        flow_error = np.abs(actual[:-1] - fine[label][:-1])
        flow_limit = 2e-5 * np.abs(fine[label][:-1]) + 2e-11 + mesh_allowance
        assert np.all(flow_error <= flow_limit), (flow_error, flow_limit)
        # The observed second-order mesh difference supplies a conservative
        # half-difference allowance, in addition to the interface's 0.002 K.
        assert actual[-1] == pytest.approx(fine[label][-1], abs=0.002 + second / 2)


def test_inlet_weight_scaling_and_repeated_call_independence():
    """Preserve results after weight scaling, reordering and another case.

    Contract: "输入" permits unnormalized nonnegative inlet weights;
    "调用、失败与资源" specifies repeated-call and dictionary-order invariance.
    """
    c = configuration(points=5)
    a = run(c)
    other = configuration("counter-current", 5)
    other["retentate"]["temperature_K"] = 710.0
    run(other)
    changed = copy.deepcopy(c)
    for side in ["retentate", "sweep"]:
        changed[side]["mole_fractions"] = {
            k: v * 7.3 for k, v in reversed(list(c[side]["mole_fractions"].items()))
        }
    b = run(changed)
    # run() already checks each profile count against the same requested grid.
    for x, y in zip(a["profiles"], b["profiles"], strict=True):
        for side in ["retentate", "sweep"]:
            assert x[side]["temperature_K"] == pytest.approx(
                y[side]["temperature_K"], abs=4e-4
            )
            assert x[side]["species_flow_kmol_s"] == pytest.approx(
                y[side]["species_flow_kmol_s"], rel=3e-6, abs=1e-13
            )


def test_hot_reverse_permeation_uses_donor_enthalpy():
    """Check coupled outlet states when hot sweep gas permeates in reverse.

    Contract: "模型与工况" requires the donor-side partial molar enthalpy
    for negative membrane flux; "输出" sets coupled temperature/flow accuracy.
    """
    c = configuration("counter-current", 9)
    c["sweep"].update(
        temperature_K=780.0, pressure_Pa=4e5, mole_fractions={"H2": 0.2, "AR": 0.8}
    )
    c["membrane"]["permeance_kmol_m2_s_Pa"] = 1e-10
    c["catalyst_area_m2"] = 20.0
    r = run(c)
    assert all(p["membrane_transfer_kmol_s_m"] < 0 for p in r["profiles"])
    coarse = solve_cells(c, 16)
    medium = solve_cells(c, 32)
    independent = solve_cells(c, 64)
    for label, index in [("retentate_outlet", -1), ("sweep_outlet", 0)]:
        side = "retentate" if label.startswith("retentate") else "sweep"
        actual = r["profiles"][index][side]
        expected = independent[label]
        names = ct.Solution(MECH, "gas").species_names
        mesh_allowance = species_mesh_budget(
            coarse[label][:-1], medium[label][:-1], expected[:-1]
        )
        flow_error = np.abs(
            np.array([actual["species_flow_kmol_s"][x] for x in names]) - expected[:-1]
        )
        flow_limit = 2e-5 * np.abs(expected[:-1]) + 2e-11 + mesh_allowance
        assert np.all(flow_error <= flow_limit), (flow_error, flow_limit)
        mesh_difference = abs(expected[-1] - medium[label][-1])
        assert mesh_difference < 0.4 * abs(medium[label][-1] - coarse[label][-1]) + 1e-7
        assert mesh_difference < 0.003
        assert actual["temperature_K"] == pytest.approx(
            expected[-1], abs=0.002 + mesh_difference / 2
        )


def test_default_mechanism_and_json_cli_are_consistent():
    """Return consistent profiles for explicit defaults, omissions and stdin.

    Contract: "输入" defines default mechanism/phase values;
    "调用、失败与资源" requires a JSON stdin/stdout command-line interface.
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
    assert cli["flow_direction"] == default["flow_direction"]
    assert len(cli["profiles"]) == len(trusted["profiles"])
    for result in (implicit, cli):
        # Counts were checked above or by run() against the requested grid.
        for actual, expected in zip(
            result["profiles"], trusted["profiles"], strict=True
        ):
            assert actual["z_m"] == expected["z_m"]
            for side in ("retentate", "sweep"):
                assert actual[side]["temperature_K"] == pytest.approx(
                    expected[side]["temperature_K"], abs=0.0004
                )
                assert actual[side]["species_flow_kmol_s"] == pytest.approx(
                    expected[side]["species_flow_kmol_s"], rel=3e-6, abs=1e-13
                )
                assert actual[side]["enthalpy_flow_W"] == pytest.approx(
                    expected[side]["enthalpy_flow_W"], rel=3e-7, abs=1e-7
                )


@pytest.mark.parametrize(
    "change",
    ["negative-flow", "unknown-species", "bad-direction", "bad-grid", "bool-pressure"],
)
def test_invalid_configuration_is_rejected(change):
    """Reject each declared invalid input with ValueError.

    Contract: "输入" requires positive flow, known species, declared flow
    direction, an increasing grid and non-boolean numbers;
    "调用、失败与资源" specifies the exception type, but not message text.
    """
    c = configuration(points=3)
    if change == "negative-flow":
        c["sweep"]["molar_flow_kmol_s"] = -1
    elif change == "unknown-species":
        c["retentate"]["mole_fractions"]["not-species"] = 0
    elif change == "bad-direction":
        c["flow_direction"] = "parallel"
    elif change == "bad-grid":
        c["sample_positions_m"] = [0, 0.05, 0.04]
    else:
        c["sweep"]["pressure_Pa"] = True
    # Only the exception type is contractual; do not require private wording.
    with pytest.raises(ValueError):  # noqa: PT011
        candidate.simulate_module(c)

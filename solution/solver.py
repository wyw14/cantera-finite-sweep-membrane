"""Author implementation: conservative molar/enthalpy-flow collocation."""

import math
import numbers
from decimal import Decimal, localcontext
import numpy as np
from scipy.integrate import solve_bvp
from reactor_app.materials import load_materials


def _linear_surface_edges(surface):
    """Recognize elementary, coverage-independent first-order site transfers.

    At fixed gas state these form a finite continuous-time Markov chain. This
    detection uses declared stoichiometry/orders, not a mechanism filename or
    numerical example; nonlinear adsorption and coverage-dependent rates fall
    back to physical-time integration.
    """
    if any(species.size != 1 for species in surface.species()):
        return None
    indices = [surface.kinetics_species_index(name) for name in surface.species_names]
    reactants = surface.reactant_stoich_coeffs[indices]
    products = surface.product_stoich_coeffs[indices]
    edges = []
    for index, reaction in enumerate(surface.reactions()):
        if type(reaction.rate).__name__ not in (
            "InterfaceArrheniusRate",
            "StickingArrheniusRate",
        ):
            return None
        if reaction.rate.coverage_dependencies:
            return None
        left, right = reactants[:, index], products[:, index]
        if (
            np.count_nonzero(left) != 1
            or np.count_nonzero(right) != 1
            or np.max(left) != 1
            or np.max(right) != 1
        ):
            return None
        for species_index, name in enumerate(surface.species_names):
            if reaction.orders.get(name, left[species_index]) != left[species_index]:
                return None
        edges.append(
            (index, int(np.argmax(left)), int(np.argmax(right)), reaction.reversible)
        )
    return edges


def _decimal_solve(matrix, right):
    """Small pivoted linear solve retaining widely separated kinetic scales."""
    count = len(right)
    rows = [list(row) + [value] for row, value in zip(matrix, right)]
    for column in range(count):
        pivot = max(range(column, count), key=lambda i: abs(rows[i][column]))
        rows[column], rows[pivot] = rows[pivot], rows[column]
        divisor = rows[column][column]
        if not divisor:
            raise RuntimeError("singular reachable surface class")
        rows[column] = [value / divisor for value in rows[column]]
        for index in range(count):
            if index != column:
                multiplier = rows[index][column]
                rows[index] = [
                    a - multiplier * b for a, b in zip(rows[index], rows[column])
                ]
    return [row[-1] for row in rows]


def _linear_surface_limit(surface, edges, initial):
    """Default-state limit, including transient classes and conserved masses.

    Closed communicating classes each have a stationary distribution. Initial
    transient probability reaches those classes with absorption probabilities
    obtained from the transient generator. Decimal summation avoids losing a
    weak physical transition when adding it to much faster transitions.
    """
    count = surface.n_species
    surface.coverages = np.full(count, 1.0 / count)
    forward = np.array(surface.forward_rates_of_progress) * count / surface.site_density
    reverse = np.array(surface.reverse_rates_of_progress) * count / surface.site_density
    transitions = []
    for index, left, right, reversible in edges:
        if left != right and forward[index] > 0:
            transitions.append((left, right, Decimal(str(float(forward[index])))))
        if left != right and reversible and reverse[index] > 0:
            transitions.append((right, left, Decimal(str(float(reverse[index])))))
    if not transitions:
        surface.coverages = initial
        return
    exponents = [rate.adjusted() for _, _, rate in transitions]
    with localcontext() as context:
        context.prec = max(60, max(exponents) - min(exponents) + 40)
        zero, one = Decimal(0), Decimal(1)
        generator = [[zero for _ in range(count)] for _ in range(count)]
        reach = np.eye(count, dtype=bool)
        for left, right, rate in transitions:
            generator[right][left] += rate
            generator[left][left] -= rate
            reach[left, right] = True
        for via in range(count):
            reach |= reach[:, via, None] & reach[None, via, :]
        groups = []
        unused = set(range(count))
        while unused:
            source = min(unused)
            group = [i for i in sorted(unused) if reach[source, i] and reach[i, source]]
            unused.difference_update(group)
            groups.append(group)
        closed = [
            group
            for group in groups
            if all(
                not reach[i, j] for i in group for j in range(count) if j not in group
            )
        ]
        transient = [i for i in range(count) if not any(i in group for group in closed)]
        starting = [Decimal(str(float(value))) for value in initial]
        residence = {}
        if transient:
            values = _decimal_solve(
                [[-generator[i][j] for j in transient] for i in transient],
                [starting[i] for i in transient],
            )
            residence = dict(zip(transient, values))
        result = [zero for _ in range(count)]
        for group in closed:
            mass = sum((starting[i] for i in group), zero)
            mass += sum(
                (generator[i][j] * residence[j] for i in group for j in transient), zero
            )
            matrix = [[generator[i][j] for j in group] for i in group]
            matrix[-1] = [one for _ in group]
            right = [zero for _ in group]
            right[-1] = one
            stationary = _decimal_solve(matrix, right)
            for index, weight in zip(group, stationary):
                result[index] = mass * weight
        surface.coverages = np.array([float(value) for value in result])


def _number(v, name, zero=False):
    if isinstance(v, bool) or not isinstance(v, numbers.Real):
        raise ValueError(name + " must be a finite real number")
    v = float(v)
    if not math.isfinite(v) or v < 0 or (not zero and v == 0):
        raise ValueError(name + " is outside its allowed range")
    return v


def _configuration(c, gas):
    if not isinstance(c, dict):
        raise ValueError("config must be a dictionary")
    try:
        L = _number(c["length_m"], "length_m")
        cat = _number(c["catalyst_area_m2"], "catalyst_area_m2", True)
        void = _number(c["retentate_void_volume_m3"], "retentate_void_volume_m3", True)
        membrane = c["membrane"]
        area = _number(membrane["area_m2"], "membrane.area_m2", True)
        permeance = _number(membrane["permeance_kmol_m2_s_Pa"], "permeance", True)
        species = membrane["species"]
        if not isinstance(species, str) or species not in gas.species_names:
            raise ValueError("unknown membrane species")
        heat = c["heat"]
        inter = _number(heat["interchannel_UA_W_K"], "interchannel_UA_W_K", True)
        wall = _number(heat["wall_UA_W_K"], "wall_UA_W_K", True)
        Tw = _number(heat["wall_temperature_K"], "wall_temperature_K")
        direction = c["flow_direction"]
        if direction not in ("co-current", "counter-current"):
            raise ValueError("invalid flow_direction")
        z = c["sample_positions_m"]
        if not isinstance(z, (list, tuple)) or not 2 <= len(z) <= 201:
            raise ValueError("two to 201 sample positions are required")
        z = [_number(v, "sample position", True) for v in z]
        if z[0] != 0 or z[-1] != L or any(a >= b for a, b in zip(z, z[1:])):
            raise ValueError("samples must increase from 0 to length_m")
        feeds = []
        for side in ("retentate", "sweep"):
            f = c[side]
            T = _number(f["temperature_K"], side + ".temperature_K")
            P = _number(f["pressure_Pa"], side + ".pressure_Pa")
            F = _number(f["molar_flow_kmol_s"], side + ".molar_flow_kmol_s")
            X = f["mole_fractions"]
            if not isinstance(X, dict) or not X:
                raise ValueError("nonempty species weights are required")
            weights = {}
            for name, value in X.items():
                if name not in gas.species_names:
                    raise ValueError("unknown feed species")
                weights[name] = _number(value, "species weight", True)
            if sum(weights.values()) <= 0:
                raise ValueError("zero composition")
            gas.TPX = T, P, weights
            feeds.append((T, P, F * np.array(gas.X), F * float(gas.enthalpy_mole)))
    except (KeyError, TypeError, IndexError) as exc:
        raise ValueError("missing or malformed configuration") from exc
    return (
        L,
        cat,
        void,
        area * permeance,
        gas.species_index(species),
        inter,
        wall,
        Tw,
        direction,
        np.array(z),
        feeds,
    )


def simulate_module(config):
    if not isinstance(config, dict):
        raise ValueError("config must be a dictionary")
    gas, sweep, surf = load_materials(config)
    if (
        gas.n_species > 8
        or gas.n_reactions > 20
        or surf.n_species > 8
        or surf.n_reactions > 30
    ):
        raise ValueError("mechanism exceeds supported size")
    L, cat, void, mem, k, UA, UAw, Tw, direction, z, feeds = _configuration(config, gas)
    n = gas.n_species
    sign = 1 if direction == "co-current" else -1
    ref = max(sum(feeds[0][2]), sum(feeds[1][2]))
    hscale = ref * 3e7
    scale = np.r_[np.full(n, ref), hscale, np.full(n, ref), hscale]
    left = np.r_[feeds[0][2], feeds[0][3], feeds[1][2], feeds[1][3]] / scale
    Pr, Ps = feeds[0][1], feeds[1][1]
    surface_default = surf.coverages.copy()
    linear_edges = _linear_surface_edges(surf)
    surface_initialized = False

    def surface_state():
        # The physical initial-value definition selects the branch. A small
        # algebraic residual alone can also describe a non-attracting blocked
        # boundary. This anchors and validates the faster trial-state tracker.
        if linear_edges is not None:
            _linear_surface_limit(surf, linear_edges, surface_default)
            return
        surf.coverages = surface_default
        previous = surface_default.copy()
        initial_rate = (
            np.max(np.abs(surf.get_net_production_rates(surf))) / surf.site_density
        )
        # This is physical-time integration, not a change of rate constants.
        # Uniformly slower kinetics have the same attracting steady state but
        # require a proportionally longer relaxation time. An absolute small
        # rate alone therefore cannot certify that the coverages have settled.
        duration = min(1.0, 1.0 / initial_rate) if initial_rate > 0 else 1.0
        for _ in range(32):
            surf.advance_coverages(duration, rtol=1e-10, atol=1e-16)
            current = surf.coverages.copy()
            creation = np.array(surf.get_creation_rates(surf)) / surf.site_density
            destruction = np.array(surf.get_destruction_rates(surf)) / surf.site_density
            net = creation - destruction
            activity = creation + destruction
            ratios = np.divide(
                np.abs(net), activity, out=np.zeros_like(net), where=activity > 0
            )
            # A vanishing species that only decays need not reach exact zero.
            ratios[(current < 1e-13) & (net <= 0)] = 0.0
            if (
                np.max(np.abs(net)) < 2e-7
                and np.max(ratios) < 2e-7
                and np.max(np.abs(current - previous)) < 2e-10
            ):
                return
            previous = current
            duration *= 10.0
        raise RuntimeError("physical surface branch did not relax to steady state")

    def point(y, coupling=1.0, report=False, verify_branch=False):
        nonlocal surface_initialized
        y = y * scale
        Fr, Hr, Fs, Hs = y[:n], y[n], y[n + 1 : 2 * n + 1], y[-1]
        # Small negative Newton trial values are projected only for property
        # evaluation. Accepted solutions are checked without this projection.
        ar = np.maximum(Fr, 0.0)
        ass = np.maximum(Fs, 0.0)
        gas.HPX = Hr / float(ar @ gas.molecular_weights), Pr, ar
        sweep.HPX = Hs / float(ass @ sweep.molecular_weights), Ps, ass
        Tr, Ts = gas.T, sweep.T
        surf.TP = Tr, Pr
        if linear_edges is not None or not surface_initialized:
            surface_state()
            surface_initialized = True
        else:
            surf.advance_coverages_to_steady_state()
        if report or verify_branch:
            tracked = surf.coverages.copy()
            surface_state()
            if not np.allclose(tracked, surf.coverages, rtol=3e-7, atol=1e-9):
                raise RuntimeError("surface continuation left the physical branch")
        sr = np.array(surf.get_net_production_rates(gas)) * cat / L
        if gas.n_reactions:
            sr += np.array(gas.net_production_rates) * void / L
        J = mem / L * (Pr * gas.X[k] - Ps * sweep.X[k])
        h = (
            gas.partial_molar_enthalpies[k]
            if J >= 0
            else sweep.partial_molar_enthalpies[k]
        )
        qi = UA / L * (Tr - Ts)
        qw = UAw / L * (Tw - Tr)
        dFr = sr.copy()
        dFr[k] -= J
        dFs = np.zeros(n)
        dFs[k] = sign * J
        dy = np.r_[dFr, qw - qi - J * h, dFs, sign * (qi + J * h)] * coupling / scale
        if not report:
            return dy

        def side(g, F, H):
            return {
                "temperature_K": float(g.T),
                "pressure_Pa": float(g.P),
                "species_flow_kmol_s": dict(zip(g.species_names, map(float, F))),
                "enthalpy_flow_W": float(H),
            }

        return {
            "retentate": side(gas, Fr, Hr),
            "sweep": side(sweep, Fs, Hs),
            "surface_coverages": dict(
                zip(surf.species_names, map(float, surf.coverages))
            ),
            "membrane_transfer_kmol_s_m": float(J),
            "interchannel_heat_W_m": float(qi),
            "wall_heat_W_m": float(qw),
        }

    def bc(a, b):
        r = a[: n + 1] - left[: n + 1]
        s = (a if sign == 1 else b)[n + 1 :] - left[n + 1 :]
        return np.r_[r, s]

    grid = np.linspace(0, L, 17)
    guess = np.repeat(left[:, None], len(grid), axis=1)
    sol = None
    try:
        for factor in (0.25, 0.6, 1.0):

            def rhs(x, Y):
                return np.column_stack(
                    [point(Y[:, i], factor) for i in range(Y.shape[1])]
                )

            sol = solve_bvp(
                rhs, bc, grid, guess, tol=2e-7, bc_tol=2e-10, max_nodes=1800
            )
            if not sol.success:
                raise RuntimeError("reactor boundary-value solution did not converge")
            grid, guess = sol.x, sol.y
        # Validate every accepted collocation node and midpoint independently
        # from the default physical initial state, not just requested outputs.
        checks = np.unique(np.r_[sol.x, (sol.x[:-1] + sol.x[1:]) / 2])
        for accepted in sol.sol(checks).T:
            point(accepted, verify_branch=True)
        values = sol.sol(z)
        rows = []
        for zz, yy in zip(z, values.T):
            physical = yy * scale
            if (
                min(np.min(physical[:n]), np.min(physical[n + 1 : 2 * n + 1]))
                < -1e-10 * ref
            ):
                raise RuntimeError("solution has negative species flow")
            row = point(yy, report=True)
            row["z_m"] = float(zz)
            rows.append(row)
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("reactor numerical solution failed") from exc
    return {"flow_direction": direction, "profiles": rows}

"""Independent verification: cell-integrated balances, temperatures as unknowns.

This does not import candidate/reference functions.  It uses implicit midpoint
finite volumes and, for counterflow, alternating directional cell sweeps.
The submitted API is a continuous model; only the converged-grid limit is used.
"""

import numpy as np
import cantera as ct
from scipy.optimize import root


def solve_cells(c, cells):
    """Return outlet states from independently integrated cell balances.

    Counterflow uses alternating sweeps; only separately converged meshes
    supply reference values for the continuous-model contract.
    """
    path = c["mechanism"]
    g = ct.Solution(path, c.get("gas_phase", "gas"), transport_model=None)
    sw = ct.Solution(path, c.get("gas_phase", "gas"), transport_model=None)
    s = ct.Interface(path, c.get("surface_phase", "Ru_surface"), adjacent=[g])
    default_coverage = s.coverages.copy()
    initialized = False
    n = g.n_species
    Pr = c["retentate"]["pressure_Pa"]
    Ps = c["sweep"]["pressure_Pa"]
    k = g.species_index(c["membrane"]["species"])
    ref = max(c[x]["molar_flow_kmol_s"] for x in ("retentate", "sweep"))
    hs = ref * 3e7

    def inlet(label, phase):
        """Normalize the declared inlet flow and temperature for iteration."""
        f = c[label]
        phase.TPX = f["temperature_K"], f["pressure_Pa"], f["mole_fractions"]
        return np.r_[phase.X * f["molar_flow_kmol_s"] / ref, phase.T / 700.0]

    r0 = inlet("retentate", g)
    s0 = inlet("sweep", sw)

    def state(phase, v, P):
        """Set local thermochemistry and return its total enthalpy flow."""
        F = np.maximum(v[:n] * ref, 1e-35)
        phase.TPX = v[-1] * 700.0, P, F
        return float(sum(F) * phase.enthalpy_mole)

    def physical_surface():
        """Relax from default coverage and require a settled physical branch."""
        s.coverages = default_coverage
        s.advance_coverages(2.0, rtol=2e-10, atol=2e-16)
        settled = False
        for duration in (8.0, 80.0, 800.0, 8000.0, 80000.0):
            before = s.coverages.copy()
            s.advance_coverages(duration, rtol=2e-10, atol=2e-16)
            rate = np.max(abs(s.get_net_production_rates(s))) / s.site_density
            if np.max(abs(s.coverages - before)) < 2e-10 and rate < 2e-7:
                settled = True
                break
        if not settled:
            raise RuntimeError("independent FV physical surface branch did not settle")

    def exchange(r, ss, validate=False):
        """Evaluate cell sources and optionally recheck the surface branch."""
        nonlocal initialized
        state(g, r, Pr)
        state(sw, ss, Ps)
        s.TP = g.T, Pr
        if not initialized:
            physical_surface()
            initialized = True
        else:
            s.advance_coverages_to_steady_state()
        if validate:
            tracked = s.coverages.copy()
            physical_surface()
            if np.max(abs(tracked - s.coverages)) > 1e-9:
                raise RuntimeError("independent FV tracked a different surface branch")
        chemistry = (
            c["catalyst_area_m2"] / cells * np.array(s.get_net_production_rates(g))
        )
        if g.n_reactions:
            chemistry += c["retentate_void_volume_m3"] / cells * g.net_production_rates
        transfer = (
            c["membrane"]["area_m2"]
            * c["membrane"]["permeance_kmol_m2_s_Pa"]
            / cells
            * (Pr * g.X[k] - Ps * sw.X[k])
        )
        donor_h = (
            g.partial_molar_enthalpies[k]
            if transfer >= 0
            else sw.partial_molar_enthalpies[k]
        )
        qi = c["heat"]["interchannel_UA_W_K"] / cells * (g.T - sw.T)
        qw = c["heat"]["wall_UA_W_K"] / cells * (c["heat"]["wall_temperature_K"] - g.T)
        return chemistry, transfer, qi, qw, donor_h

    def residual_pair(rin, rout, sin, sout):
        """Return both cells' species and energy balance residuals."""
        chemistry, j, qi, qw, h = exchange((rin + rout) / 2, (sin + sout) / 2)
        dr = (rout[:n] - rin[:n]) * ref - chemistry
        dr[k] += j
        ds = (sout[:n] - sin[:n]) * ref
        ds[k] -= j
        er = state(g, rout, Pr) - state(g, rin, Pr) - qw + qi + j * h
        es = state(sw, sout, Ps) - state(sw, sin, Ps) - qi - j * h
        return np.r_[dr / ref, er / hs, ds / ref, es / hs]

    R = np.tile(r0, (cells + 1, 1))
    S = np.tile(s0, (cells + 1, 1))
    if c["flow_direction"] == "co-current":
        for i in range(cells):
            rrout, ssout = R[i].copy(), S[i].copy()
            for _iteration in range(40):
                before = np.r_[rrout, ssout]
                rr = root(
                    lambda x, i=i, ssout=ssout: residual_pair(R[i], x, S[i], ssout)[
                        : n + 1
                    ],
                    rrout,
                    tol=1e-10,
                )
                if np.max(np.abs(rr.fun)) > 1e-8:
                    raise RuntimeError("independent FV coflow retentate failure")
                rrout = rr.x
                ss = root(
                    lambda x, i=i, rrout=rrout: residual_pair(R[i], rrout, S[i], x)[
                        n + 1 :
                    ],
                    ssout,
                    tol=1e-10,
                )
                if np.max(np.abs(ss.fun)) > 1e-8:
                    raise RuntimeError("independent FV coflow sweep failure")
                ssout = ss.x
                if np.max(abs(np.r_[rrout, ssout] - before)) < 2e-10:
                    break
            else:
                raise RuntimeError("independent FV coflow coupling failure")
            R[i + 1], S[i + 1] = rrout, ssout
    else:
        for _iteration in range(80):
            before = np.r_[R.ravel(), S.ravel()]
            for i in range(cells):
                rr = root(
                    lambda x, i=i: residual_pair(R[i], x, S[i + 1], S[i])[: n + 1],
                    R[i],
                    tol=1e-10,
                )
                if np.max(np.abs(rr.fun)) > 1e-8:
                    raise RuntimeError("independent FV retentate failure")
                R[i + 1] = rr.x
            for i in range(cells - 1, -1, -1):
                rr = root(
                    lambda x, i=i: residual_pair(R[i], R[i + 1], S[i + 1], x)[n + 1 :],
                    S[i + 1],
                    tol=1e-10,
                )
                if np.max(np.abs(rr.fun)) > 1e-8:
                    raise RuntimeError("independent FV sweep failure")
                S[i] = rr.x
            if np.max(np.abs(np.r_[R.ravel(), S.ravel()] - before)) < 2e-10:
                break
        else:
            raise RuntimeError("independent FV coupling failure")
    # Every accepted cell source is independently checked against the defined
    # physical initial-value limit, after nonlinear trial iterations finish.
    for i in range(cells):
        exchange((R[i] + R[i + 1]) / 2, (S[i] + S[i + 1]) / 2, validate=True)
    return {
        "retentate_outlet": np.r_[R[-1, :n] * ref, R[-1, -1] * 700.0],
        "sweep_outlet": np.r_[
            (S[-1] if c["flow_direction"] == "co-current" else S[0])[:n] * ref,
            (S[-1] if c["flow_direction"] == "co-current" else S[0])[-1] * 700.0,
        ],
    }

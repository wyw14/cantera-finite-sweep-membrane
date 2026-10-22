"""Author implementation: conservative molar/enthalpy-flow collocation."""
import math
import numbers
import numpy as np
from scipy.integrate import solve_bvp
from reactor_app.materials import load_materials

def _number(v, name, zero=False):
    if isinstance(v, bool) or not isinstance(v, numbers.Real):
        raise ValueError(name + ' must be a finite real number')
    v = float(v)
    if not math.isfinite(v) or v < 0 or (not zero and v == 0):
        raise ValueError(name + ' is outside its allowed range')
    return v

def _configuration(c, gas):
    if not isinstance(c, dict):
        raise ValueError('config must be a dictionary')
    try:
        L = _number(c['length_m'], 'length_m')
        cat = _number(c['catalyst_area_m2'], 'catalyst_area_m2', True)
        void = _number(c['retentate_void_volume_m3'], 'retentate_void_volume_m3', True)
        membrane = c['membrane']
        area = _number(membrane['area_m2'], 'membrane.area_m2', True)
        permeance = _number(membrane['permeance_kmol_m2_s_Pa'], 'permeance', True)
        species = membrane['species']
        if not isinstance(species, str) or species not in gas.species_names:
            raise ValueError('unknown membrane species')
        heat = c['heat']
        inter = _number(heat['interchannel_UA_W_K'], 'interchannel_UA_W_K', True)
        wall = _number(heat['wall_UA_W_K'], 'wall_UA_W_K', True)
        Tw = _number(heat['wall_temperature_K'], 'wall_temperature_K')
        direction = c['flow_direction']
        if direction not in ('co-current', 'counter-current'):
            raise ValueError('invalid flow_direction')
        z = c['sample_positions_m']
        if not isinstance(z, (list, tuple)) or not 2 <= len(z) <= 201:
            raise ValueError('two to 201 sample positions are required')
        z = [_number(v, 'sample position', True) for v in z]
        if z[0] != 0 or z[-1] != L or any(a >= b for a,b in zip(z,z[1:])):
            raise ValueError('samples must increase from 0 to length_m')
        feeds = []
        for side in ('retentate', 'sweep'):
            f = c[side]
            T = _number(f['temperature_K'], side+'.temperature_K')
            P = _number(f['pressure_Pa'], side+'.pressure_Pa')
            F = _number(f['molar_flow_kmol_s'], side+'.molar_flow_kmol_s')
            X = f['mole_fractions']
            if not isinstance(X, dict) or not X:
                raise ValueError('nonempty species weights are required')
            weights = {}
            for name, value in X.items():
                if name not in gas.species_names:
                    raise ValueError('unknown feed species')
                weights[name] = _number(value, 'species weight', True)
            if sum(weights.values()) <= 0:
                raise ValueError('zero composition')
            gas.TPX = T, P, weights
            feeds.append((T,P,F*np.array(gas.X),F*float(gas.enthalpy_mole)))
    except (KeyError, TypeError, IndexError) as exc:
        raise ValueError('missing or malformed configuration') from exc
    return L,cat,void,area*permeance,gas.species_index(species),inter,wall,Tw,direction,np.array(z),feeds

def simulate_module(config):
    if not isinstance(config, dict):
        raise ValueError('config must be a dictionary')
    gas, sweep, surf = load_materials(config)
    if gas.n_species > 8 or gas.n_reactions > 20 or surf.n_species > 8 or surf.n_reactions > 30:
        raise ValueError('mechanism exceeds supported size')
    L,cat,void,mem,k,UA,UAw,Tw,direction,z,feeds = _configuration(config, gas)
    n = gas.n_species
    sign = 1 if direction == 'co-current' else -1
    ref = max(sum(feeds[0][2]), sum(feeds[1][2]))
    hscale = ref*3e7
    scale = np.r_[np.full(n,ref), hscale, np.full(n,ref), hscale]
    left = np.r_[feeds[0][2],feeds[0][3],feeds[1][2],feeds[1][3]] / scale
    Pr,Ps = feeds[0][1],feeds[1][1]
    surface_default = surf.coverages.copy()
    surface_initialized = False

    def surface_state():
        # The physical initial-value definition selects the branch. A small
        # algebraic residual alone can also describe a non-attracting blocked
        # boundary. This anchors and validates the faster trial-state tracker.
        surf.coverages = surface_default
        previous = surface_default.copy()
        for duration in (1., 10., 100., 1000., 10000., 100000.):
            surf.advance_coverages(duration, rtol=1e-10, atol=1e-16)
            current = surf.coverages.copy()
            residual = np.max(np.abs(surf.get_net_production_rates(surf))) / surf.site_density
            if residual < 2e-7 and np.max(np.abs(current-previous)) < 2e-10:
                return
            previous = current
        raise RuntimeError('physical surface branch did not relax to steady state')

    def point(y, coupling=1., report=False, verify_branch=False):
        nonlocal surface_initialized
        y = y*scale
        Fr,Hr,Fs,Hs = y[:n],y[n],y[n+1:2*n+1],y[-1]
        # Small negative Newton trial values are projected only for property
        # evaluation. Accepted solutions are checked without this projection.
        ar = np.maximum(Fr, 1e-30*ref)
        ass = np.maximum(Fs, 1e-30*ref)
        gas.HPX = Hr/float(ar@gas.molecular_weights), Pr, ar
        sweep.HPX = Hs/float(ass@sweep.molecular_weights), Ps, ass
        Tr,Ts = gas.T,sweep.T
        surf.TP = Tr,Pr
        if not surface_initialized:
            surface_state()
            surface_initialized = True
        else:
            surf.advance_coverages_to_steady_state()
        if report or verify_branch:
            tracked = surf.coverages.copy()
            surface_state()
            if not np.allclose(tracked, surf.coverages, rtol=3e-7, atol=1e-9):
                raise RuntimeError('surface continuation left the physical branch')
        sr = np.array(surf.get_net_production_rates(gas))*cat/L
        if gas.n_reactions:
            sr += np.array(gas.net_production_rates)*void/L
        J = mem/L*(Pr*gas.X[k]-Ps*sweep.X[k])
        h = gas.partial_molar_enthalpies[k] if J >= 0 else sweep.partial_molar_enthalpies[k]
        qi = UA/L*(Tr-Ts)
        qw = UAw/L*(Tw-Tr)
        dFr = sr.copy(); dFr[k] -= J
        dFs = np.zeros(n); dFs[k] = sign*J
        dy = np.r_[dFr,qw-qi-J*h,dFs,sign*(qi+J*h)]*coupling/scale
        if not report:
            return dy
        def side(g,F,H):
            return {'temperature_K':float(g.T),'pressure_Pa':float(g.P),
                    'species_flow_kmol_s':dict(zip(g.species_names,map(float,F))),
                    'enthalpy_flow_W':float(H)}
        return {'retentate':side(gas,Fr,Hr), 'sweep':side(sweep,Fs,Hs),
                'surface_coverages':dict(zip(surf.species_names,map(float,surf.coverages))),
                'membrane_transfer_kmol_s_m':float(J),
                'interchannel_heat_W_m':float(qi),'wall_heat_W_m':float(qw)}

    def bc(a,b):
        r = a[:n+1]-left[:n+1]
        s = (a if sign == 1 else b)[n+1:]-left[n+1:]
        return np.r_[r,s]

    grid = np.linspace(0,L,17)
    guess = np.repeat(left[:,None],len(grid),axis=1)
    sol = None
    try:
        for factor in (.25,.6,1.):
            def rhs(x,Y):
                return np.column_stack([point(Y[:,i],factor) for i in range(Y.shape[1])])
            sol = solve_bvp(rhs,bc,grid,guess,tol=2e-7,bc_tol=2e-10,max_nodes=1800)
            if not sol.success:
                raise RuntimeError('reactor boundary-value solution did not converge')
            grid,guess = sol.x,sol.y
        # Validate every accepted collocation node and midpoint independently
        # from the default physical initial state, not just requested outputs.
        checks = np.unique(np.r_[sol.x, (sol.x[:-1]+sol.x[1:])/2])
        for accepted in sol.sol(checks).T:
            point(accepted, verify_branch=True)
        values = sol.sol(z)
        rows = []
        for zz,yy in zip(z,values.T):
            physical = yy*scale
            if min(np.min(physical[:n]),np.min(physical[n+1:2*n+1])) < -1e-10*ref:
                raise RuntimeError('solution has negative species flow')
            row = point(yy,report=True)
            row['z_m'] = float(zz)
            rows.append(row)
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError('reactor numerical solution failed') from exc
    return {'flow_direction':direction,'profiles':rows}

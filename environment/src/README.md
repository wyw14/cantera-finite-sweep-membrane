# Finite-sweep catalytic membrane application

This application extends the physical setting of Cantera's published one-dimensional
packed-bed catalytic membrane example. The new finite-sweep interface is an authored
feature request, not an upstream bug report or a claim about plant measurements.

Existing files:

- `candidate.py`: JSON and Python API entry point.
- `reactor_app/materials.py`: mechanism/phase adapter and single-inlet inspector.
- `reactor_app/solver.py`: new simulation backend to implement.
- `upstream/1D_packed_bed.py`: unmodified Cantera 3.2.0 example, retained as context.
- `upstream/ammonia-Ru-Ba-YSZ-CSM-2019.yaml`: published research mechanism used by it.

The upstream example requires scikits.odes/IDA and matplotlib. Those optional example
dependencies are not installed or required by this application. The task uses installed
Cantera, NumPy, SciPy and a controlled-pressure model; the original pressure-drop model
is outside this extension. See instruction.md for the actual input/output contract.

Cantera source: https://github.com/Cantera/cantera/tree/v3.2.0
Mechanism repository: https://github.com/Cantera/cantera-example-data
Original mechanism publication: Zhang et al. (2019),
https://doi.org/10.1021/acssuschemeng.9b04929
The mechanism's research attribution is retained in its header. Cantera's source-code
license does not purport to grant rights on behalf of the mechanism's original authors.

# Finite-sweep catalytic membrane application

The application extends Cantera's one-dimensional packed-bed catalytic membrane example with a finite second flow channel and controlled pressures.

- `candidate.py`: Python and JSON entry point.
- `reactor_app/materials.py`: mechanism loading and inlet properties.
- `reactor_app/solver.py`: simulation backend to implement.
- `upstream/1D_packed_bed.py`: Cantera 3.2.0 example.
- `upstream/ammonia-Ru-Ba-YSZ-CSM-2019.yaml`: associated research mechanism.

The application uses Cantera, NumPy and SciPy. The upstream example's optional IDA and plotting dependencies are not required. Mechanism attribution and licensing are retained in `upstream/ATTRIBUTION.md` and the mechanism header.

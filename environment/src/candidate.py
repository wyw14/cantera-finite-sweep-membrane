"""Public entry point for the finite-sweep membrane-reactor application."""
from reactor_app.solver import simulate_module

if __name__ == '__main__':
    import json
    import sys
    print(json.dumps(simulate_module(json.load(sys.stdin)), allow_nan=False))

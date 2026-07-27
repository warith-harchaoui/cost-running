"""
Infrastructure layer: adapters to the outside world.

Module summary
--------------
This package holds everything the domain and application layers deliberately
avoid: the Green Algorithms energy estimator and its hardware tables, the
operating-system power profilers, repository detection, and file-system access.
Each module is a self-contained adapter that a use case calls; none of them
imports a delivery surface.

Author
------
Project maintainers.
"""

from __future__ import annotations

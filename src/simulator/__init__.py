"""inferia-sim: a discrete-event simulator of LLM inference serving.

Users -> requests -> routing -> GPU / KV-cache behaviour -> results, with every
number labelled SIMULATED (docs/platform/interfaces.md §1). See
``simulator.config`` for the scenario schema and ``inferia-sim --help``.
"""

__version__ = "0.1.0"

from simulator.config import Scenario, load_scenario  # noqa: E402

__all__ = ["Scenario", "load_scenario", "__version__"]

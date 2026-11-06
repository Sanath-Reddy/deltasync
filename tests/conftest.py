import os

from hypothesis import HealthCheck, settings

# CI runs 200 examples; run with HYPOTHESIS_PROFILE=thorough before closing a milestone.
settings.register_profile("ci", max_examples=200, deadline=None)
settings.register_profile(
    "thorough",
    max_examples=10_000,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci"))

"""Catalyst sensitivity signal family.

- ``catalyst.event_study.v1`` (BASELINE): empirical event-study
  sensitivity per (instrument, event_subject) pair, applied to
  upcoming events with linear time-decay.
- ``catalyst.causal.v1`` (SHADOW, gated on EconML): causal-inference
  CATE for conditional sensitivities.
"""

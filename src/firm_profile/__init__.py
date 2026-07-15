"""
firm_profile — build a vendor profile from a firm's OWN federal award history.

WS-2 of Shot A: replaces the synthetic Meridian mock with a data-sourced profile so
candidates are scored against the firm's real NAICS/PSC/agency/value footprint.
Every profile is stamped ``data_source: "UEI:<uei>"`` (never SYNTHETIC).
"""

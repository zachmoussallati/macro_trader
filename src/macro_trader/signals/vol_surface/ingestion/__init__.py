"""Options-chain ingestion subpackage (Stage 5).

The :class:`OptionsChainIngester` abstract base defines the contract
every provider implementation must satisfy. v1 ships with
:class:`YfinanceOptionsIngester`; paid-source ingesters (Polygon,
Alpha Vantage, ORATS, etc.) plug in as additional subclasses with
no schema / runner / dashboard changes.
"""

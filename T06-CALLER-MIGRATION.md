# Caller migration

Calls use one llmcall request with inherited model, effort and routing defaults.
Explicit user selections remain constraints. Timeouts, uncertain effects and
invalid outputs never cause a second business model call. Synthetic tests do
not certify production capabilities.

The classifier, topic judge and quality review already use llmcall. Explicit
configured chains and deterministic classification fallback remain. The legacy
DEFAULT_CHAIN export stays only for import compatibility. em_tick.py retains
its existing overlay byte for byte.

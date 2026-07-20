# TSMOM replacement snapshot amendment required

The historical snapshot referenced by the earlier validation is not recoverable.
A replacement snapshot cannot inherit that snapshot's identity: it will be a new,
independently hashed snapshot with a new canonical identity.

This pull request supplies only preregistered snapshot-v2 serialization,
manifest, archive, package, and materialization infrastructure. It downloads no
market data and publishes no real package. A following, independent pull request
must perform and audit the real acquisition and publication. Only a subsequent
pull request may amend and preregister TSMOM against that new immutable identity.

No SIT, TSMOM, or other investment strategy is run by this infrastructure work,
and it makes no investment-performance claim.

"""Client-facing API surfaces.

A surface translates between the shape a client speaks and the OpenAI shape
used internally. It is the mirror of a provider adapter, which translates
between an upstream's shape and the same internal one.

    client  -> [surface in ] -> internal (OpenAI) -> [provider out] -> upstream
    client <-  [surface out] <- internal (OpenAI) <- [provider in ] <- upstream

Keeping them separate is what lets a new client format cost a translator rather
than a second request pipeline.
"""

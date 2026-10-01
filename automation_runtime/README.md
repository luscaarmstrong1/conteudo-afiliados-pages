# Pinterest RSS watchdog

This directory contains only public catalog data and a standard-library Python generator. GitHub Actions runs `python automation_runtime/cloud_watchdog.py` every two hours. It counts today's RSS items in `America/Sao_Paulo`, fills missing daily slots with deterministic `/go/` and `/posts/` URLs, and leaves complete feeds unchanged. The Windows runner uses the same generator and refreshes catalogs from local Cloudinary image records.

`publication_config.json` controls the phase. Phase 1 is 10 items per account daily. Phases 2 and 3 are available but require an explicit edit. The Pinterest Business accounts must keep their respective RSS feeds connected; this runtime never calls Pinterest.

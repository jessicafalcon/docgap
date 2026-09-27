# Open DAMIR fixture

One file per processing month, `A202501.csv` to `A202503.csv`: the header and the
source lines the offline sample keeps at a rate of 1 in 5,000, byte for byte and
in source order. `loader/offline_sample.py` cuts them and `loader/sample.lock` pins
their row counts and SHA-256; ADR 0013 states the rule.

Source: [Open DAMIR](https://www.data.gouv.fr/datasets/open-damir-base-complete-sur-les-depenses-dassurance-maladie-interregimes),
published by the Caisse nationale de l'Assurance Maladie under the
[Licence Ouverte](https://www.etalab.gouv.fr/licence-ouverte-open-licence), last
updated on 30 March 2026 by the page's metadata (checked 27 September 2026). The
monthly files are the versions pinned by SHA-256 in `loader/sources.lock`,
downloaded on 26 and 27 September 2026. These files are under the Licence Ouverte,
not the repository's MIT licence.

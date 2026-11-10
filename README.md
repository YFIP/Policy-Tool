# Veille juridique et administrative – Your Friend in Paris

A bilingual (FR/EN toggle) web page listing each week's official news relevant to YFIP clients
(immigration/ANEF, Assurance maladie, taxes, housing, work, family),
updated automatically every Monday morning (Paris time).

**Sources:** Service-Public.gouv.fr (particuliers + professionnels),
ministère de l'Intérieur (immigration, ANEF, actualités), ameli.fr.
Légifrance can be added later via the PISTE API.

## How it works
`.github/workflows/veille.yml` runs `policy_digest/collect.py` every Monday.
It reads the sources, keeps new items matching `policy_digest/keywords.json`,
and rebuilds `docs/index.html`, which GitHub Pages publishes.

## Changing what it watches
- **Topics/keywords:** edit `policy_digest/keywords.json`.
- **Sources:** edit `policy_digest/sources.json`.
- **Something not showing?** Check the yellow "Sources non lues" box on the
  page, or the latest run in the Actions tab.

Public government news only. No client data is stored or processed here.

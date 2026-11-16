# Veille juridique et administrative – Your Friend in Paris

A bilingual (FR/EN toggle) web page listing each week's official news relevant to YFIP clients
(immigration/ANEF, Assurance maladie, taxes, housing, work, family),
updated automatically every Monday morning (Paris time).

**Sources:** Service-Public.gouv.fr (particuliers + professionnels),
the Journal officiel via the Légifrance API (new laws, ordinances, and
relevant decrees/arrêtés), plus quick links to the Interior Ministry and ameli.fr.

## How it works
`.github/workflows/veille.yml` runs `policy_digest/collect.py` every Monday.
It reads the sources, keeps new items matching `policy_digest/keywords.json`,
and rebuilds `docs/index.html`, which Netlify publishes (see `netlify.toml`).

## Légifrance (Journal officiel) setup – one time
1. Create an account on https://piste.gouv.fr and log in.
2. Under *Applications*, create an application (sandbox is fine to start, then production).
3. In the *API* catalogue, subscribe that application to **Légifrance** and accept the terms (CGU).
4. Copy the application's **OAuth client_id** and **client_secret**.
5. In this GitHub repo: *Settings > Secrets and variables > Actions > New repository secret*:
   `LEGIFRANCE_CLIENT_ID` and `LEGIFRANCE_CLIENT_SECRET`.
   (Using sandbox keys? Also add a *variable* or secret `LEGIFRANCE_ENV` = `sandbox`.)
6. *Actions > Veille juridique > Run workflow* to test. The run log shows a
   `✓ Légifrance – Journal officiel` line with how many texts were read.

New texts appear in their own section, "Nouveaux textes au Journal officiel",
tagged with any client topic they match. What's kept is set in
`policy_digest/sources.json`: laws and ordinances always; decrees and arrêtés
only if they match `keywords.json`; appointments, naturalisation lists and similar
individual measures are excluded.

## Changing what it watches
- **Topics/keywords:** edit `policy_digest/keywords.json`.
- **Sources:** edit `policy_digest/sources.json` (for Légifrance: which natures
  are always kept, which need a keyword match, and title words to exclude).
- **Something not showing?** Check the yellow "Sources non lues" box on the
  page, or the latest run in the Actions tab.

Public government news only. No client data is stored or processed here.

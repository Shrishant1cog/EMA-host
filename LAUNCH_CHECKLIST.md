# EMA 2.0.1 Launch Checklist

**Release status: BLOCKED. Do not publish the production release until every required item is verified.**

## Website and UX

- [x] Favicon file is present and linked from the dashboard, sign-in, admin, privacy, and terms pages.
- [x] Public privacy and terms pages are present, responsive, and linked from the public website and sign-in page.
- [x] Remove the synthetic welcome banner and its marketing copy. Operational totals remain data-driven.
- [x] Flatten decorative gradients, remove the card hover lift and sign-in sheen, and reduce expensive blur and animation effects.
- [x] Prefer system fonts to avoid external font downloads.
- [x] Add automated checks for the favicon, public legal pages, absence of the Made with AI tag, and absence of cursor-tracking animation handlers.
- [ ] Run the responsive browser QA suite against the final branch for phone, tablet, laptop, and wide desktop viewports.
- [ ] Perform keyboard navigation, zoom, reduced-motion, and screen-reader spot checks.

## Domain and integrations

- [ ] Connect the chosen custom domain in Netlify and configure its DNS records.
- [ ] Verify HTTPS and redirect behavior on the custom domain.
- [ ] Add the final custom-domain origin and exact OAuth callback URL in Google Cloud OAuth settings, then test sign-in and sign-out end to end.
- [ ] Test live Gmail reading, attachment extraction, Calendar create/update/duplicate prevention, Drive persistence, and revocation with production-like credentials.
- [ ] Confirm hosting environment variables, allowed origins, backend health checks, and error reporting on the final domain.
- [ ] Review the Privacy Policy and Terms of Service against the actual deployed data flow and obtain appropriate legal review before public launch.

## Release gate

Keep the release blocked until the custom domain, HTTPS, Google OAuth callback, and live integration tests have been verified. A passing code-level CI run alone does not confirm that production integrations or domain routing work.

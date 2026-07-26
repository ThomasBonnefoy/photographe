# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Photographers (indépendants, small studios) who need a bilingual portfolio website to showcase their work, attract clients, and manage their online presence without technical skills or a backend.

## Product Purpose

Éclats d'Instants is a self-hosted, no-backend website template that lets photographers launch a beautiful bilingual portfolio with dynamic gallery, video, testimonial, and pricing sections managed entirely through a password-protected admin panel in the browser (localStorage).

## Positioning

A photographer's portfolio site that requires zero server setup, zero database, and zero deployment cost — yet feels like a premium custom-built site. The admin panel stores all content in the browser, making it the only portfolio template a photographer can fully manage by opening a single HTML file.

## Operating Context

- The photographer or their client opens `index.html` in a browser to view the public site
- The photographer opens `admin.html`, enters a password, and manages gallery images (add/delete/reorder), videos, testimonials, pricing copy, and contact details
- Data persists in the browser's `localStorage` — no backend, no API, no database
- The site is fully bilingual (FR/EN) with toggle persisted across sessions
- Deployment is a simple static file upload to any web host (Netlify, Vercel, GitHub Pages, or FTP to any shared host)

## Capabilities and Constraints

**Existing capabilities:**
- Bilingual (FR/EN) one-page portfolio with hero slideshow, about, gallery with lightbox and category filter, stats counter, three pricing plans, testimonials grid, video grid, contact form, floating CTA
- Admin panel with login protection (sessionStorage), CRUD for gallery/videos/testimonials, drag-free reorder, data export/import as JSON, reset to defaults, password change
- Animations: aperture clip-path reveal, Ken Burns hero zoom, scroll reveal, staggered nav entrance (all respect `prefers-reduced-motion`)

**Known gaps (to resolve in future work):**
- Contact form has no backend integration — must be connected to Formspree, EmailJS, or an equivalent service
- All current content (name "Thomas", location "Lyon", pricing, phone, email, photos, testimonials) is demo placeholder — the template consumer replaces everything
- Data storage is browser-only (localStorage); no multi-device sync, no cloud backup, no user accounts
- No blog, no online booking/calendar, no client gallery/proofing
- No SEO metadata customization per-photo or per-page
- Mobile nav uses a full-screen overlay with no animation refinement

**Undecided:**
- Whether to add a backend or keep fully static (localStorage with optional JSON export for backup)
- Whether to add multi-photographer/studio accounts
- Pricing model for the template itself (one-time sale, subscription, free with paid services)

## Brand Commitments

- Business name: **Éclats d'Instants**
- Tagline: *Photographie de mariages & événements*
- Color: gold accent `#C4A06A` as the signature color
- Fonts: Playfair Display (serif, headings) + Inter (sans-serif, body) — this pair is established and may be changed during redesign
- Bilingual by default (FR primary, EN secondary)
- Voice: warm, professional, emotionally resonant — "capturer l'authenticité" / "capture authenticity"
- Login default password: `admin123` (configurable)

## Evidence on Hand

- `index.html` — full public site implementation (~1647 lines, single file)
- `admin.html` — full admin panel implementation (~983 lines, single file)
- All photography assets are placeholder (picsum.photos demo images)
- All testimonial text, names, events are demo content
- Contact phone `06 12 34 56 78` and email `bonjour@eclats-instants.fr` are demo
- Pricing (350€, 890€, Sur devis) is demo
- No real client photos, videos, testimonials, or case studies are included
- Social links (Instagram, Facebook, YouTube, Pinterest) are placeholder (`#`)

## Product Principles

1. **Zero-backend by default.** A photographer should be able to launch the site by uploading two HTML files. Any service integration (forms, booking) must be an opt-in add-on, never a requirement.
2. **The photographer controls everything.** The admin panel must be intuitive enough that someone with no technical background can update their portfolio in under a minute.
3. **Bilingual is not a feature toggle — it's the baseline.** Every visible text string must have both FR and EN versions; adding a third language must not require structural changes.
4. **Demo content looks real, but is obviously replaceable.** Placeholder content should demonstrate the full visual and functional range of the template so the buyer immediately sees what's possible.
5. **Offline-first, durable.** The site must work fully when served from any static host, a local `file://` path, or even offline (after first load). localStorage is the persistence layer; JSON export/import is the backup layer.

## Accessibility & Inclusion

- Focus-visible outlines on all interactive elements (`#C4A06A` ring)
- Skip navigation link
- ARIA roles on gallery (`role="list"`, `role="listitem"`), filter buttons (`role="tab"`, `aria-selected`), lightbox (`role="dialog"`, `aria-modal`)
- `prefers-reduced-motion` respected for all animations
- Color contrast: gold `#C4A06A` on dark or light backgrounds requires verification (not audited yet)
- No `aria-live` regions for dynamic content updates
- Form validation uses browser-native `alert()` — should be improved to inline error messages
